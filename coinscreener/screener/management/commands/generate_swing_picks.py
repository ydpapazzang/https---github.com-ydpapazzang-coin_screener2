import math
import time
from collections import Counter

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
from coinscreener.screener.price_format import format_krw_price
from coinscreener.screener.recommendation_versioning import (
    SWING_STRATEGY_VERSION,
    json_number,
    recommendation_snapshot,
)
from coinscreener.screener.swing_strategy import (
    ENTRY_VALID_DAYS,
    MAX_ALREADY_CROSSED_PCT,
    MAX_ATR_PCT,
    MAX_DAILY_JUMP_PCT,
    MAX_ENTRY_GAP_PCT,
    MAX_HOLD_DAYS,
    MAX_OPEN_POSITIONS,
    MAX_STOP_DISTANCE_PCT,
    MIN_COMPLETED_CANDLES,
    MIN_STOP_DISTANCE_PCT,
    RISK_PER_TRADE_PCT,
    build_swing_recommendation,
    rank_swing_recommendations,
    validate_btc_regime,
)


UPBIT_MARKETS_URL = 'https://api.upbit.com/v1/market/all'

SWING_PARAMETERS = {
    'min_completed_daily_candles': MIN_COMPLETED_CANDLES,
    'max_entry_gap_pct': MAX_ENTRY_GAP_PCT,
    'max_already_crossed_pct': MAX_ALREADY_CROSSED_PCT,
    'max_daily_jump_pct': MAX_DAILY_JUMP_PCT,
    'max_atr_pct': MAX_ATR_PCT,
    'min_stop_distance_pct': MIN_STOP_DISTANCE_PCT,
    'max_stop_distance_pct': MAX_STOP_DISTANCE_PCT,
    'entry_valid_days': ENTRY_VALID_DAYS,
    'max_hold_days': MAX_HOLD_DAYS,
    'max_open_positions': MAX_OPEN_POSITIONS,
    'risk_per_trade_pct': RISK_PER_TRADE_PCT,
    'partial_exit_r_multiple': 2,
    'partial_exit_fraction': 0.5,
    'liquidity_candidate_limit': 30,
}


def _rejection_category(reason):
    """Collapse per-price rejection text into displayable diagnostic categories."""
    categories = (
        ('종가·EMA20·EMA60 상승 정렬', 'EMA20·EMA60 상승 정렬 미충족'),
        ('최근 20일 모멘텀', '20일 모멘텀 미충족'),
        ('ATR 변동성', 'ATR 변동성 과다'),
        ('진입가가 현재가보다', '진입가가 현재가보다 2% 이상 높음'),
        ('현재가가 진입가를', '돌파가를 1% 넘겨 추격 진입 제외'),
        ('당일 급등률', '당일 10% 이상 급등'),
        ('필요 손절 폭', '손절 폭 과다'),
        ('일봉 데이터', '일봉 데이터 부족'),
        ('현재가가', '현재가 데이터 오류'),
    )
    for prefix, category in categories:
        if reason.startswith(prefix):
            return category
    return reason


def _format_no_candidate_reason(candidate_count, rejected, data_error_count):
    """Return a compact rest-day reason suitable for the public result card."""
    parts = [
        f'{reason} {count}건'
        for reason, count in rejected.most_common(4)
    ]
    if data_error_count:
        parts.append(f'API·데이터 오류 {data_error_count}건')
    if not parts:
        return '분석 가능한 후보가 없어 추천을 생성하지 못했습니다.'
    return f'후보 {candidate_count}개 분석 결과: ' + ' · '.join(parts) + '.'


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
        runtime_dir = settings.RUNTIME_DIR
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return runtime_dir / 'generate_swing_picks.lock'

    @staticmethod
    def _market_regime_snapshot(regime=None, label='btc_daily_uptrend', reason=''):
        regime = regime or {}
        result = {
            'label': label,
            'reason': reason,
        }
        for key in (
            'close', 'ema20', 'ema20_5d_ago', 'ema60', 'atr14',
            'momentum20_pct', 'change_3d_pct', 'breakout_20',
            'median_value_20',
        ):
            if key in regime:
                result[f'btc_daily_{key}'] = json_number(regime[key])
        return result

    @staticmethod
    def _snapshot(market_regime, data_as_of, recommendation=None):
        parameters = dict(SWING_PARAMETERS)
        if recommendation:
            parameters.update({
                'momentum20_pct': json_number(recommendation.get('momentum20_pct')),
                'trend_strength_pct': json_number(recommendation.get('trend_strength_pct')),
                'atr_pct': json_number(recommendation.get('atr_pct')),
                'entry_gap_pct': json_number(recommendation.get('entry_gap_pct')),
                'stop_distance_pct': json_number(recommendation.get('stop_distance_pct')),
                'median_value_20': json_number(recommendation.get('median_value_20')),
            })
        return recommendation_snapshot(
            SWING_STRATEGY_VERSION,
            parameters,
            market_regime,
            data_as_of,
        )

    def _record_rest_day(
        self, today_date, reason, market_regime=None, data_as_of=None
    ):
        market_regime = market_regime or self._market_regime_snapshot(
            label='rejected', reason=reason
        )
        data_as_of = data_as_of or timezone.now()
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
                **self._snapshot(market_regime, data_as_of),
            )
        return True

    @staticmethod
    def _recommendation_model(
        today_date, recommendation, market_regime=None, data_as_of=None
    ):
        market_regime = market_regime or {'label': 'btc_daily_uptrend'}
        data_as_of = data_as_of or timezone.now()
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
            **Command._snapshot(
                market_regime, data_as_of, recommendation
            ),
        )

    def _persist_recommendations(
        self, today_date, recommendations, market_regime=None, data_as_of=None
    ):
        objects = [
            self._recommendation_model(
                today_date, recommendation, market_regime, data_as_of
            )
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
        data_as_of = timezone.now()

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
            market_regime = self._market_regime_snapshot(
                label='rejected', reason=reason
            )
            if self._record_rest_day(
                today_date, reason, market_regime, data_as_of
            ):
                self.stdout.write(self.style.WARNING(
                    f"[{today_date}] 스윙 휴식: {reason}"
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f"[{today_date}] 다른 스윙 결과가 먼저 저장되어 종료합니다."
                ))
            return
        except Exception as exc:
            raise CommandError(f"BTC 시장 국면 확인 실패: {exc}") from exc

        market_regime = self._market_regime_snapshot(regime)

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
        liquidity_data_errors = 0
        self.stdout.write("20일 거래대금 상위 스윙 후보를 찾습니다...")
        for ticker in tickers:
            if ticker in excluded_tickers:
                continue
            try:
                candles = pyupbit.get_ohlcv(ticker, interval='day', count=21)
                if candles is None or len(candles) < 21 or 'value' not in candles:
                    liquidity_data_errors += 1
                    continue
                median_value = float(
                    candles.iloc[:-1]['value'].astype(float).median()
                )
                if math.isfinite(median_value) and median_value > 0:
                    liquidity.append((ticker, median_value))
            except Exception:
                liquidity_data_errors += 1
                continue
            time.sleep(0.05)

        liquidity.sort(key=lambda item: item[1], reverse=True)
        candidates = []
        rejected = Counter()
        analysis_data_errors = 0
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
                rejected[_rejection_category(str(exc))] += 1
                self.stdout.write(self.style.WARNING(
                    f"[{ticker}] 스윙 추천 제외: {exc}"
                ))
            except Exception as exc:
                analysis_data_errors += 1
                self.stdout.write(self.style.ERROR(
                    f"[{ticker}] 스윙 분석 오류: {exc}"
                ))
            time.sleep(0.1)

        recommendations = rank_swing_recommendations(
            candidates,
            limit=available_slots,
        )
        if not recommendations:
            candidate_count = min(len(liquidity), 30)
            data_error_count = liquidity_data_errors + analysis_data_errors
            reason = _format_no_candidate_reason(
                candidate_count, rejected, data_error_count,
            )
            market_regime['candidate_diagnostics'] = {
                'candidate_count': candidate_count,
                'rejections': dict(rejected),
                'data_error_count': data_error_count,
            }
            if self._record_rest_day(
                today_date, reason, market_regime, data_as_of
            ):
                self.stdout.write(self.style.WARNING(
                    f"[{today_date}] 스윙 휴식: {reason}"
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f"[{today_date}] 다른 스윙 결과가 먼저 저장되어 종료합니다."
                ))
            return

        if not self._persist_recommendations(
            today_date, recommendations, market_regime, data_as_of
        ):
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
                f"({recommendation['ticker']})\n"
                f"   진입가 {format_krw_price(recommendation['entry_price'])}원\n"
                f"   1차 목표 {format_krw_price(recommendation['target_price'])}원 "
                "(2R·50% 익절)\n"
                "   2차 청산 EMA20·3ATR 추적손절 (최장 20일)\n"
                f"   초기 손절 {format_krw_price(recommendation['stop_loss'])}원"
            )
        message_lines.append(
            "\n※ 진입 신호는 2일간 유효하며 1회 위험 한도는 자산의 0.5%입니다."
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

