import math
import time
import pandas as pd
import pyupbit
from django.core.management.base import BaseCommand
from django.utils import timezone
from coinscreener.screener.models import DailyRecommendation
from coinscreener.screener.daily_picks import (
    K_CANDIDATES,
    MAX_ENTRY_GAP_PCT,
    MAX_PREVIOUS_RANGE_MULTIPLIER,
    MIN_BACKTEST_TRADES,
    MIN_BACKTEST_WIN_RATE,
    RecommendationRejected,
    build_recommendation,
    is_stablecoin_ticker,
    rank_recommendations,
)
from coinscreener.screener.recommendation_versioning import (
    DANTA_STRATEGY_VERSION,
    json_number,
    recommendation_snapshot,
)


DANTA_PARAMETERS = {
    'k_candidates': list(K_CANDIDATES),
    'min_backtest_trades': MIN_BACKTEST_TRADES,
    'min_backtest_win_rate_pct': MIN_BACKTEST_WIN_RATE,
    'target_pct': 2.0,
    'stop_loss_pct': 1.5,
    'max_entry_gap_pct': MAX_ENTRY_GAP_PCT,
    'max_previous_range_multiplier': MAX_PREVIOUS_RANGE_MULTIPLIER,
    'btc_ema_fast': 20,
    'btc_ema_slow': 60,
    'btc_1h_drop_limit_pct': -1.5,
    'btc_15m_rsi_period': 14,
    'btc_15m_rsi_min': 50,
    'liquidity_candidate_limit': 10,
    'recommendation_limit': 3,
}

class Command(BaseCommand):
    help = 'KST 오전 9시 기준 거래대금 상위 후보를 안전 기준으로 검증해 단타 종목을 추천합니다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='KST 09:00~10:59 생성 시간 제한을 무시합니다.',
        )

    @staticmethod
    def _snapshot(market_regime, data_as_of, extra_parameters=None):
        parameters = dict(DANTA_PARAMETERS)
        if extra_parameters:
            parameters.update(extra_parameters)
        return recommendation_snapshot(
            DANTA_STRATEGY_VERSION,
            parameters,
            market_regime,
            data_as_of,
        )

    def _record_rest_day(self, today_date, reason, market_regime, data_as_of):
        DailyRecommendation.objects.create(
            date=today_date,
            trade_type='danta',
            coin_ticker='SKIP',
            coin_name='단타휴식',
            entry_price=0,
            target_price=0,
            stop_loss=0,
            k_value=0,
            reason=reason,
            status='skipped',
            **self._snapshot(market_regime, data_as_of),
        )

    def handle(self, *args, **options):
        now_kst = timezone.localtime(timezone.now())
        today_date = now_kst.date()
        data_as_of = timezone.now()
        market_regime = {'label': 'evaluating'}

        if not options.get('force') and not 9 <= now_kst.hour < 11:
            self.stdout.write(self.style.WARNING(
                f"[{now_kst:%Y-%m-%d %H:%M KST}] 추천 생성 시간(09:00~10:59)이 아니므로 스킵합니다."
            ))
            return

        # 이미 오늘 자 추천이 있으면 스킵 (중복 방지)
        if DailyRecommendation.objects.filter(date=today_date, trade_type='danta').exists():
            self.stdout.write(self.style.WARNING(f"[{today_date}] 이미 오늘의 추천 코인이 생성되어 있습니다. 스킵합니다."))
            return

        self.stdout.write(self.style.SUCCESS(f"[{today_date}] 오늘의 단타 추천 코인 생성을 시작합니다..."))

        self.stdout.write("비트코인(BTC) 시장 상황 필터링 중...")
        try:
            # 1. BTC 1시간봉 EMA20 > EMA60 및 최근 1시간 하락폭 체크
            btc_60m = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=100)
            if btc_60m is not None and len(btc_60m) > 60:
                ema20 = btc_60m['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                ema60 = btc_60m['close'].ewm(span=60, adjust=False).mean().iloc[-1]
                btc_change = (btc_60m['close'].iloc[-1] - btc_60m['close'].iloc[-2]) / btc_60m['close'].iloc[-2] * 100
                market_regime.update({
                    'btc_1h_close': json_number(btc_60m['close'].iloc[-1]),
                    'btc_1h_ema20': json_number(ema20),
                    'btc_1h_ema60': json_number(ema60),
                    'btc_1h_change_pct': json_number(btc_change),
                })
                if ema20 <= ema60:
                    reason_msg = f"BTC 1시간봉 역배열 상태 (EMA20 < EMA60)"
                    market_regime['label'] = 'btc_1h_downtrend'
                    self.stdout.write(self.style.WARNING(f"[{today_date}] {reason_msg}. 오늘은 단타를 쉬는 날입니다."))
                    self._record_rest_day(today_date, reason_msg, market_regime, data_as_of)
                    return

                # 최근 1시간 하락폭 체크 (직전 종가 대비 현재가 등락률)
                if btc_change <= -1.5:
                    reason_msg = f"BTC 최근 1시간 급락 (하락폭 {btc_change:.2f}%)"
                    market_regime['label'] = 'btc_1h_sharp_drop'
                    self.stdout.write(self.style.WARNING(f"[{today_date}] {reason_msg}. 오늘은 단타를 쉬는 날입니다."))
                    self._record_rest_day(today_date, reason_msg, market_regime, data_as_of)
                    return
            else:
                self.stdout.write(self.style.ERROR("BTC 1시간봉 데이터를 가져오지 못했습니다."))
                return

            # 2. BTC 15분봉 RSI > 50 체크 (engine.calculate_rsi와 동일한 Wilder 방식 사용)
            from coinscreener.screener.engine import calculate_rsi
            btc_15m = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=100)
            if btc_15m is not None and len(btc_15m) > 15:
                current_rsi = calculate_rsi(btc_15m, 14).iloc[-1]
                if pd.isna(current_rsi):
                    self.stdout.write(self.style.ERROR("BTC 15분봉 RSI 계산 결과가 유효하지 않습니다."))
                    return

                market_regime['btc_15m_rsi14'] = json_number(current_rsi)
                if current_rsi <= 50:
                    reason_msg = f"BTC 15분봉 매수심리 악화 (RSI {current_rsi:.1f} <= 50)"
                    market_regime['label'] = 'btc_15m_weak_momentum'
                    self.stdout.write(self.style.WARNING(f"[{today_date}] {reason_msg}. 오늘은 단타를 쉬는 날입니다."))
                    self._record_rest_day(today_date, reason_msg, market_regime, data_as_of)
                    return
            else:
                self.stdout.write(self.style.ERROR("BTC 15분봉 데이터를 가져오지 못했습니다."))
                return

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"BTC 필터 확인 중 오류 발생: {e}"))
            return

        self.stdout.write(self.style.SUCCESS("BTC 필터 통과! 종목 스캔을 시작합니다."))
        market_regime['label'] = 'btc_intraday_filter_passed'

        # 1. 전일 거래대금 상위 코인 찾기 (업비트 원화 마켓 전체 조회)
        tickers = pyupbit.get_tickers(fiat="KRW")
        vol_data = []

        self.stdout.write("코인별 전일 거래대금 스캔 중...")
        # API Rate Limit을 고려하여 일봉 데이터 최신 2개만 가져옴
        for ticker in tickers or []:
            if is_stablecoin_ticker(ticker):
                continue
            try:
                df = pyupbit.get_ohlcv(ticker, interval="day", count=2)
                if df is not None and len(df) >= 2:
                    # df.iloc[-2] 가 전일 데이터, df.iloc[-1] 이 당일 데이터(09시 이후 진행중)
                    prev_day = df.iloc[-2]
                    value = float(prev_day['value'])
                    if math.isfinite(value) and value > 0:
                        vol_data.append({
                            'ticker': ticker,
                            'value': value,
                        })
            except Exception:
                pass
            time.sleep(0.05) # Rate limit

        # 거래대금 상위 10개를 모두 평가한 뒤 백테스트 성과로 최종 3개를 선정한다.
        vol_data.sort(key=lambda x: x['value'], reverse=True)
        top_candidates = [x['ticker'] for x in vol_data[:10]]
        volume_by_ticker = {x['ticker']: x['value'] for x in vol_data[:10]}

        recommendations = []

        # 2. 상위 후보들의 최근 14일 백테스트와 당일 진입가 안전 기준 검증
        for ticker in top_candidates:
            try:
                df = pyupbit.get_ohlcv(ticker, interval="day", count=16)
                current_price = pyupbit.get_current_price(ticker)
                kr_name = ticker.replace('KRW-', '')

                recommendation = build_recommendation(
                    ticker=ticker,
                    coin_name=kr_name,
                    df=df,
                    current_price=current_price,
                    today_date=today_date,
                )
                recommendation['volume_value'] = volume_by_ticker[ticker]
                recommendation['reason'] = (
                    "전일 거래대금 상위 후보. "
                    f"최근 14일 백테스트 K={recommendation['k_value']}에서 "
                    f"{recommendation['backtest_trades']}회 거래, "
                    f"승률 {recommendation['win_rate']:.1f}%, "
                    f"누적 {recommendation['backtest_profit']:.1f}%. "
                    f"생성 시점 현재가 대비 진입가 차이 {recommendation['entry_gap_pct']:.1f}%."
                )
                recommendations.append(recommendation)
            except RecommendationRejected as e:
                self.stdout.write(self.style.WARNING(
                    f"[{ticker}] 추천 제외: {e}"
                ))
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f"Error processing {ticker}: {e}"
                ))
            time.sleep(0.1)

        recommendations = rank_recommendations(recommendations, limit=3)
        if not recommendations:
            reason_msg = "현재가 괴리·변동폭·백테스트 안전 기준을 통과한 종목이 없습니다."
            self._record_rest_day(
                today_date, reason_msg, market_regime, data_as_of
            )
            self.stdout.write(self.style.WARNING(
                f"[{today_date}] {reason_msg}"
            ))
            return

        # 3. DB에 저장
        for rec in recommendations:
            DailyRecommendation.objects.create(
                date=today_date,
                trade_type='danta',
                coin_ticker=rec['ticker'],
                coin_name=rec['name'],
                entry_price=rec['entry_price'],
                target_price=rec['target_price'],
                stop_loss=rec['stop_loss'],
                k_value=rec['k_value'],
                reason=rec['reason'],
                status='pending',
                **self._snapshot(
                    market_regime,
                    data_as_of,
                    {
                        'selected_k': rec['k_value'],
                        'backtest_trades': rec['backtest_trades'],
                        'backtest_win_rate_pct': json_number(rec['win_rate']),
                        'backtest_total_pct': json_number(rec['backtest_profit']),
                        'entry_gap_pct': json_number(rec['entry_gap_pct']),
                        'previous_day_value_krw': json_number(rec['volume_value']),
                    },
                ),
            )
            self.stdout.write(self.style.SUCCESS(f"추천 등록 완료: {rec['ticker']} (진입가: {rec['entry_price']})"))
            
        # 4. 텔레그램 메시지 발송
        if recommendations:
            from coinscreener.screener.telegram import send_message
            msg_lines = ["🔥 오늘의 단타 AI 추천 코인 🔥\n"]
            for i, rec in enumerate(recommendations, 1):
                msg_lines.append(f"{i}. <b>{rec['name']}</b> ({rec['ticker']})")
            
            # Woniiscreener 링크 안내
            msg_lines.append("\n👉 <a href='https://woniiscreener.duckdns.org/danta/'>웹사이트에서 타점 확인하기</a>")
            
            message_text = "\n".join(msg_lines)
            res = send_message(message_text)
            if res.get('ok'):
                self.stdout.write(self.style.SUCCESS("텔레그램 발송 성공"))
            else:
                self.stdout.write(self.style.ERROR(f"텔레그램 발송 실패: {res.get('error')}"))

        self.stdout.write(self.style.SUCCESS("오늘의 단타 추천 스크립트 실행이 완료되었습니다."))

