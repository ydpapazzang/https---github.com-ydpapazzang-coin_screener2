import math
import time

import pyupbit
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from filelock import FileLock, Timeout

from coinscreener.screener.daily_picks import (
    RecommendationRejected,
    is_stablecoin_ticker,
)
from coinscreener.screener.models import DailyRecommendation
from coinscreener.screener.swing_strategy import (
    MAX_OPEN_POSITIONS,
    RISK_PER_TRADE_PCT,
    build_swing_recommendation,
    rank_swing_recommendations,
    validate_btc_regime,
)


UPBIT_MARKETS_URL = 'https://api.upbit.com/v1/market/all'


class Command(BaseCommand):
    help = 'BTC 일봉 상승 국면에서 추세 돌파형 스윙 추천을 생성합니다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='KST 09:00~10:59 생성 시간 제한을 무시합니다.',
        )

    def _safe_krw_tickers(self):
        response = requests.get(
            UPBIT_MARKETS_URL,
            params={'isDetails': 'true'},
            timeout=10,
        )
        response.raise_for_status()
        markets = response.json()
        return [
            market['market']
            for market in markets
            if market.get('market', '').startswith('KRW-')
            and market.get('market_warning', 'NONE') == 'NONE'
            and not is_stablecoin_ticker(market['market'])
        ]

    @staticmethod
    def _generation_lock_path():
        runtime_dir = settings.BASE_DIR / '.runtime'
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return runtime_dir / 'generate_swing_picks.lock'

    def _record_rest_day(self, today_date, reason):
        with transaction.atomic():
            if DailyRecommendation.objects.filter(
                date=today_date,
                trade_type='swing',
            ).exists():
                return False
            DailyRecommendation.objects.create(
                date=today_date,
                trade_type='swing',
                coin_ticker='SKIP',
                coin_name='스윙휴식',
                entry_price=0,
                target_price=0,
                stop_loss=0,
                initial_stop_loss=0,
                k_value=0,
                reason=reason,
                status='skipped',
            )
        return True

    @staticmethod
    def _recommendation_model(today_date, recommendation):
        return DailyRecommendation(
            date=today_date,
            trade_type='swing',
            coin_ticker=recommendation['ticker'],
            coin_name=recommendation['name'],
            entry_price=recommendation['entry_price'],
            target_price=recommendation['target_price'],
            stop_loss=recommendation['stop_loss'],
            initial_stop_loss=recommendation['stop_loss'],
            entry_expires_on=recommendation['entry_expires_on'],
            k_value=0,
            reason=recommendation['reason'],
            status='pending',
        )

    def _persist_recommendations(self, today_date, recommendations):
        objects = [
            self._recommendation_model(today_date, recommendation)
            for recommendation in recommendations
        ]
        with transaction.atomic():
            # 긴 API 분석 중 다른 경로에서 결과가 생겼더라도 덮어쓰지 않는다.
            if DailyRecommendation.objects.filter(
                date=today_date,
                trade_type='swing',
            ).exists():
                return False
            DailyRecommendation.objects.bulk_create(objects)
        return True

    def handle(self, *args, **options):
        lock = FileLock(str(self._generation_lock_path()), timeout=0)
        try:
            with lock:
                return self._generate(*args, **options)
        except Timeout:
            self.stdout.write(self.style.WARNING(
                "다른 프로세스에서 스윙 추천을 생성 중입니다."
            ))
            return

    def _generate(self, *args, **options):
        now_kst = timezone.localtime(timezone.now())
        today_date = now_kst.date()

        if not options.get('force') and not 9 <= now_kst.hour < 11:
            self.stdout.write(self.style.WARNING(
                f"[{now_kst:%Y-%m-%d %H:%M KST}] "
                "스윙 추천 생성 시간(09:00~10:59)이 아니므로 스킵합니다."
            ))
            return

        if DailyRecommendation.objects.filter(
            date=today_date,
            trade_type='swing',
        ).exists():
            self.stdout.write(self.style.WARNING(
                f"[{today_date}] 이미 오늘의 스윙 추천 결과가 있습니다."
            ))
            return

        open_recommendations = DailyRecommendation.objects.filter(
            trade_type='swing',
            status__in=['pending', 'active', 'partial'],
        )
        available_slots = MAX_OPEN_POSITIONS - open_recommendations.count()
        if available_slots <= 0:
            self.stdout.write(self.style.WARNING(
                "스윙 보유·대기 종목이 3개여서 신규 추천을 생성하지 않습니다."
            ))
            return

        self.stdout.write("BTC 스윙 시장 국면을 확인합니다...")
        try:
            btc_daily = pyupbit.get_ohlcv(
                'KRW-BTC', interval='day', count=181
            )
            regime = validate_btc_regime(btc_daily, today_date)
        except RecommendationRejected as exc:
            reason = str(exc)
            self._record_rest_day(today_date, reason)
            self.stdout.write(self.style.WARNING(
                f"[{today_date}] 스윙 휴식: {reason}"
            ))
            return
        except Exception as exc:
            raise CommandError(f"BTC 시장 국면 확인 실패: {exc}") from exc

        try:
            tickers = self._safe_krw_tickers()
        except Exception as exc:
            raise CommandError(f"업비트 안전 종목 목록 조회 실패: {exc}") from exc

        excluded_tickers = set(
            open_recommendations.exclude(coin_ticker='SKIP').values_list(
                'coin_ticker', flat=True
            )
        )
        liquidity = []
        self.stdout.write("20일 거래대금 상위 스윙 후보를 찾습니다...")
        for ticker in tickers:
            if ticker in excluded_tickers:
                continue
            try:
                candles = pyupbit.get_ohlcv(ticker, interval='day', count=21)
                if candles is None or len(candles) < 21 or 'value' not in candles:
                    continue
                median_value = float(
                    candles.iloc[:-1]['value'].astype(float).median()
                )
                if math.isfinite(median_value) and median_value > 0:
                    liquidity.append((ticker, median_value))
            except Exception:
                continue
            time.sleep(0.05)

        liquidity.sort(key=lambda item: item[1], reverse=True)
        candidates = []
        for ticker, _median_value in liquidity[:30]:
            try:
                candles = pyupbit.get_ohlcv(
                    ticker, interval='day', count=181
                )
                current_price = pyupbit.get_current_price(ticker)
                recommendation = build_swing_recommendation(
                    ticker=ticker,
                    coin_name=ticker.replace('KRW-', ''),
                    df=candles,
                    current_price=current_price,
                    today_date=today_date,
                )
                recommendation['reason'] = (
                    "BTC 일봉 상승 국면. "
                    f"20일 모멘텀 {recommendation['momentum20_pct']:.1f}%, "
                    f"EMA60 대비 추세 강도 {recommendation['trend_strength_pct']:.1f}%, "
                    f"ATR {recommendation['atr_pct']:.1f}%. "
                    f"진입가 괴리 {recommendation['entry_gap_pct']:.1f}%, "
                    f"초기 손절 폭 {recommendation['stop_distance_pct']:.1f}%. "
                    f"2R에서 50% 부분익절 후 추적손절. "
                    f"1회 위험 한도는 자산의 {RISK_PER_TRADE_PCT:.1f}%."
                )
                candidates.append(recommendation)
            except RecommendationRejected as exc:
                self.stdout.write(self.style.WARNING(
                    f"[{ticker}] 스윙 추천 제외: {exc}"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[{ticker}] 스윙 분석 오류: {exc}"
                ))
            time.sleep(0.1)

        recommendations = rank_swing_recommendations(
            candidates,
            limit=available_slots,
        )
        if not recommendations:
            reason = "유동성·추세·진입 괴리·변동성 기준을 통과한 종목이 없습니다."
            self._record_rest_day(today_date, reason)
            self.stdout.write(self.style.WARNING(
                f"[{today_date}] 스윙 휴식: {reason}"
            ))
            return

        if not self._persist_recommendations(today_date, recommendations):
            self.stdout.write(self.style.WARNING(
                f"[{today_date}] 생성 중 다른 스윙 결과가 먼저 저장되어 종료합니다."
            ))
            return

        for recommendation in recommendations:
            self.stdout.write(self.style.SUCCESS(
                f"스윙 추천 등록: {recommendation['ticker']} "
                f"(진입 {recommendation['entry_price']}, "
                f"손절 {recommendation['stop_loss']}, "
                f"2R {recommendation['target_price']})"
            ))

        from coinscreener.screener.telegram import send_message

        message_lines = ["📈 오늘의 스윙 AI 추천 코인\n"]
        for index, recommendation in enumerate(recommendations, 1):
            message_lines.append(
                f"{index}. <b>{recommendation['name']}</b> "
                f"({recommendation['ticker']})"
            )
        message_lines.append(
            "\n👉 <a href='https://woniiscreener.duckdns.org/swing/'>"
            "웹사이트에서 스윙 전략 확인하기</a>"
        )
        result = send_message("\n".join(message_lines))
        if result.get('ok'):
            self.stdout.write(self.style.SUCCESS("스윙 텔레그램 발송 성공"))
        else:
            self.stdout.write(self.style.ERROR(
                f"스윙 텔레그램 발송 실패: {result.get('error')}"
            ))

        self.stdout.write(self.style.SUCCESS(
            f"BTC 추세 강도 {regime['close'] / regime['ema60'] * 100 - 100:.1f}%. "
            "스윙 추천 생성이 완료되었습니다."
        ))
