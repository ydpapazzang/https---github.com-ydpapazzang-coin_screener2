import math
import time
import datetime
import pyupbit
import pybithumb
import FinanceDataReader as fdr
import pandas as pd
from django.core.management.base import BaseCommand
from django.core.cache import cache
from coinscreener.screener.models import Condition

class Command(BaseCommand):
    help = '5분마다 조건식에 사용된 타임프레임의 업비트, 빗썸, KOSPI(ETF) 데이터를 수집하여 캐시에 저장합니다.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting 24/7 Crypto & ETF Cache Bot..."))
        
        while True:
            start_time = time.time()
            self._run_crawler()
            elapsed = time.time() - start_time
            
            # 300초(5분) 주기 (수집에 걸린 시간을 빼고 휴식)
            sleep_time = max(0, 300 - elapsed)
            self.stdout.write(f"Cycle finished in {elapsed:.1f}s. Sleeping for {sleep_time:.1f}s...")
            time.sleep(sleep_time)

    def _run_crawler(self):
        try:
            # 전략 조건 유무와 관계없이 단타 성적은 매 주기 추적한다.
            self._monitor_recommendations()

            active_timeframes = set(Condition.objects.values_list('timeframe', flat=True).distinct())
            if not active_timeframes:
                self.stdout.write("활성화된 조건식이 없어 시세 캐시 수집만 생략합니다.")
                return

            # (D) 저장 시점에 지표를 미리 계산해 넣기 위한 타임프레임별 지표 명세 수집
            from coinscreener.screener.engine import indicator_specs_by_timeframe
            specs_by_tf = indicator_specs_by_timeframe(list(Condition.objects.all()))

            # 거래소 코인 및 ETF 목록 가져오기
            upbit_tickers = pyupbit.get_tickers(fiat="KRW")
            bithumb_tickers = pybithumb.get_tickers()

            self.stdout.write(f"업비트 {len(upbit_tickers)}개, 빗썸 {len(bithumb_tickers)}개, {len(active_timeframes)}개 타임프레임 수집 시작...")

            # 업비트 수집
            for ticker in upbit_tickers:
                for tf in active_timeframes:
                    self._fetch_and_cache(ticker, tf, exchange='upbit', specs=specs_by_tf.get(tf))

            # 빗썸 수집
            for ticker in bithumb_tickers:
                for tf in active_timeframes:
                    self._fetch_and_cache(ticker, tf, exchange='bithumb', specs=specs_by_tf.get(tf))


        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during crawl cycle: {e}"))

    def _monitor_recommendations(self):
        from django.utils import timezone
        from coinscreener.screener.models import DailyRecommendation

        today_date = timezone.localtime().date()

        # 목표 달성 종목도 당일 종료까지 추적해 실제 관측 최고가를 보존한다.
        tracked_recs = DailyRecommendation.objects.filter(
            date=today_date,
            status__in=['pending', 'active', 'success']
        )
        if not tracked_recs.exists():
            return

        self.stdout.write("오늘의 단타 추천 코인 성적 추적 중...")
        for rec in tracked_recs:
            try:
                current_price = float(pyupbit.get_current_price(rec.coin_ticker))
                if not math.isfinite(current_price) or current_price <= 0:
                    continue

                if rec.status == 'pending' and current_price >= rec.entry_price:
                    rec.status = 'active'
                    self.stdout.write(self.style.SUCCESS(
                        f"[{rec.coin_ticker}] 진입가 도달! (상태: 매수완료)"
                    ))

                if rec.status in ('active', 'success'):
                    # 단순 현재가 샘플보다 정확하게 직전 1분봉의 장중 고가도 반영한다.
                    observed_high = current_price
                    minute_candle = pyupbit.get_ohlcv(
                        rec.coin_ticker, interval='minute1', count=1
                    )
                    if minute_candle is not None and not minute_candle.empty:
                        candle_high = float(minute_candle.iloc[-1]['high'])
                        if math.isfinite(candle_high) and candle_high > 0:
                            observed_high = max(observed_high, candle_high)

                    if rec.highest_price is None or observed_high > rec.highest_price:
                        rec.highest_price = observed_high
                    if rec.lowest_price is None or current_price < rec.lowest_price:
                        rec.lowest_price = current_price

                # 체결 상태 판정은 기존처럼 현재가 기준으로 유지한다.
                if rec.status == 'active':
                    if current_price >= rec.target_price:
                        rec.status = 'success'
                        rec.result_pct = (
                            (rec.target_price - rec.entry_price)
                            / rec.entry_price
                            * 100
                        )
                        self.stdout.write(self.style.SUCCESS(
                            f"[{rec.coin_ticker}] 목표가 달성! "
                            f"(상태: 목표달성, 수익: {rec.result_pct:.2f}%)"
                        ))
                    elif current_price <= rec.stop_loss:
                        rec.status = 'failed'
                        rec.result_pct = (
                            (rec.stop_loss - rec.entry_price)
                            / rec.entry_price
                            * 100
                        )
                        self.stdout.write(self.style.WARNING(
                            f"[{rec.coin_ticker}] 손절가 이탈... "
                            f"(상태: 손절이탈, 손실: {rec.result_pct:.2f}%)"
                        ))

                rec.save()
            except Exception:
                pass
            time.sleep(0.1)

    def _fetch_and_cache(self, ticker, timeframe, exchange='upbit', specs=None):
        # API Rate Limit 준수 (거래소별 조절)
        if exchange == 'kospi':
            time.sleep(0.10)
        else:
            time.sleep(0.12)
        
        try:
            df = None
            if exchange == 'upbit':
                df = pyupbit.get_ohlcv(ticker, interval=timeframe, count=200)
            elif exchange == 'bithumb':
                bithumb_tf_map = {'minute15': 'minute5', 'minute30': 'minute30', 'minute60': 'hour', 'minute240': 'hour', 'day': 'day', 'week': 'day', 'month': 'day'}
                btf = bithumb_tf_map.get(timeframe, 'day')
                df = pybithumb.get_ohlcv(ticker, interval=btf)
                if df is not None and not df.empty:
                    if timeframe == 'minute15': df = df.resample('15min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif timeframe == 'minute240': df = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif timeframe == 'week': df = df.resample('W-MON').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif timeframe == 'month': df = df.resample('ME').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    df = df.tail(200)
            elif exchange == 'kospi':
                df = fdr.DataReader(ticker)
                if df is not None and not df.empty:
                    df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
                    if timeframe == 'week': df = df.resample('W-MON').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif timeframe == 'month': df = df.resample('ME').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    df = df.tail(200)

            if df is not None and not df.empty:
                df.index.name = None

                # (D) 저장 직전 지표 컬럼 사전계산 → 웹 검색은 계산을 건너뛰고 값만 읽음
                from coinscreener.screener.engine import prewarm_indicators, save_ohlcv_cache
                prewarm_indicators(df, specs)

                save_ohlcv_cache(ticker, timeframe, df)

                cache_key = f"ohlcv_{ticker}_{timeframe}_200"
                from coinscreener.screener.engine import max_cache_age
                cache.set(cache_key, df, min(180, max_cache_age(timeframe)))
                
        except Exception as e:
            pass
