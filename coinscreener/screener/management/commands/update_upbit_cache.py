import math
import threading
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
    help = '코인 OHLCV 캐시를 반복 수집하고 단타 추천 성적은 독립적으로 60초마다 추적합니다.'

    MONITOR_INTERVAL_SECONDS = 60

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting 24/7 Crypto & ETF Cache Bot..."))

        # 전체 마켓 수집은 20분 이상 걸릴 수 있으므로 단타 추적은 독립 스레드로 실행한다.
        monitor_stop = threading.Event()
        monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(monitor_stop,),
            name='daily-picks-monitor',
            daemon=True,
        )
        monitor_thread.start()
        self.stdout.write(self.style.SUCCESS(
            f"Daily picks monitor started ({self.MONITOR_INTERVAL_SECONDS}s interval)."
        ))

        try:
            while True:
                start_time = time.monotonic()
                self._run_crawler()
                elapsed = time.monotonic() - start_time

                # 300초(5분) 주기 (수집에 걸린 시간을 빼고 휴식)
                sleep_time = max(0, 300 - elapsed)
                self.stdout.write(
                    f"Cycle finished in {elapsed:.1f}s. Sleeping for {sleep_time:.1f}s..."
                )
                monitor_stop.wait(sleep_time)
        finally:
            monitor_stop.set()
            monitor_thread.join(timeout=5)

    def _monitor_loop(self, stop_event):
        from django.db import close_old_connections

        while not stop_event.is_set():
            started_at = time.monotonic()
            close_old_connections()
            try:
                self._monitor_recommendations()
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"Daily picks monitor error: {exc}"
                ))
            finally:
                close_old_connections()

            elapsed = time.monotonic() - started_at
            wait_seconds = max(0, self.MONITOR_INTERVAL_SECONDS - elapsed)
            stop_event.wait(wait_seconds)

    def _run_crawler(self):
        try:
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
        """단타와 스윙 추천을 각각의 규칙으로 추적한다."""
        self._monitor_danta_recommendations()
        self._monitor_swing_recommendations()

    def _monitor_danta_recommendations(self):
        from django.utils import timezone
        from coinscreener.screener.models import DailyRecommendation

        today_date = timezone.localtime().date()

        # 목표 달성 종목도 당일 종료까지 추적해 실제 관측 최고가를 보존한다.
        tracked_recs = DailyRecommendation.objects.filter(
            date=today_date,
            trade_type='danta',
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
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[{rec.coin_ticker}] 단타 추적 오류: {exc}"
                ))
            time.sleep(0.1)

    @staticmethod
    def _swing_result_pct(rec, exit_price):
        final_return = (exit_price - rec.entry_price) / rec.entry_price * 100
        if rec.partial_exit_price is None:
            return final_return

        partial_return = (
            (rec.partial_exit_price - rec.entry_price)
            / rec.entry_price
            * 100
        )
        return partial_return * 0.5 + final_return * 0.5

    def _close_swing(self, rec, exit_price, reason, now_kst):
        rec.exit_price = exit_price
        rec.exit_at = now_kst
        rec.exit_reason = reason
        rec.result_pct = self._swing_result_pct(rec, exit_price)
        rec.status = 'success' if rec.result_pct > 0 else 'failed'
        rec.save()
        self.stdout.write(self.style.SUCCESS(
            f"[{rec.coin_ticker}] 스윙 종료({reason}), "
            f"최종 {rec.result_pct:.2f}%"
        ))

    def _monitor_swing_recommendations(self):
        from django.utils import timezone
        from coinscreener.screener.models import DailyRecommendation
        from coinscreener.screener.swing_strategy import MAX_HOLD_DAYS

        tracked_recs = DailyRecommendation.objects.filter(
            trade_type='swing',
            status__in=['pending', 'active', 'partial'],
        )
        if not tracked_recs.exists():
            return

        now_kst = timezone.localtime()
        today_date = now_kst.date()
        self.stdout.write("스윙 추천 종목 성적 추적 중...")

        for rec in tracked_recs:
            try:
                if (
                    rec.status == 'pending'
                    and rec.entry_expires_on
                    and today_date > rec.entry_expires_on
                ):
                    rec.status = 'closed'
                    rec.exit_reason = 'entry_expired'
                    rec.exit_at = now_kst
                    rec.save()
                    continue

                current_price = float(pyupbit.get_current_price(rec.coin_ticker))
                if not math.isfinite(current_price) or current_price <= 0:
                    continue

                observed_high = current_price
                observed_low = current_price
                minute_candle = pyupbit.get_ohlcv(
                    rec.coin_ticker, interval='minute1', count=1
                )
                if minute_candle is not None and not minute_candle.empty:
                    candle = minute_candle.iloc[-1]
                    candle_high = float(candle['high'])
                    candle_low = float(candle['low'])
                    if math.isfinite(candle_high) and candle_high > 0:
                        observed_high = max(observed_high, candle_high)
                    if math.isfinite(candle_low) and candle_low > 0:
                        observed_low = min(observed_low, candle_low)

                if rec.status == 'pending':
                    if observed_high < rec.entry_price:
                        continue
                    rec.status = 'active'
                    rec.entered_at = now_kst
                    rec.highest_price = max(rec.entry_price, observed_high)
                    rec.lowest_price = min(rec.entry_price, observed_low)
                    rec.save()
                    self.stdout.write(self.style.SUCCESS(
                        f"[{rec.coin_ticker}] 스윙 진입가 도달"
                    ))

                if rec.highest_price is None or observed_high > rec.highest_price:
                    rec.highest_price = observed_high
                if rec.lowest_price is None or observed_low < rec.lowest_price:
                    rec.lowest_price = observed_low

                # 한 캔들에서 손절과 목표가가 함께 관측되면 보수적으로 손절 우선.
                if observed_low <= rec.stop_loss:
                    self._close_swing(
                        rec, min(rec.stop_loss, current_price), 'stop_loss', now_kst
                    )
                    continue

                if rec.status == 'active' and observed_high >= rec.target_price:
                    rec.status = 'partial'
                    rec.partial_exit_price = rec.target_price
                    rec.partial_exit_at = now_kst
                    rec.save()
                    self.stdout.write(self.style.SUCCESS(
                        f"[{rec.coin_ticker}] 2R 도달, 50% 부분익절"
                    ))

                entered_date = (
                    timezone.localtime(rec.entered_at).date()
                    if rec.entered_at
                    else rec.date
                )
                daily = pyupbit.get_ohlcv(
                    rec.coin_ticker, interval='day', count=61
                )
                if daily is not None and len(daily) >= 21:
                    completed = daily.iloc[:-1]
                    close = completed['close'].astype(float)
                    high = completed['high'].astype(float)
                    low = completed['low'].astype(float)
                    ema20 = float(
                        close.ewm(span=20, adjust=False).mean().iloc[-1]
                    )
                    previous_close = close.shift(1)
                    true_range = pd.concat(
                        [
                            high - low,
                            (high - previous_close).abs(),
                            (low - previous_close).abs(),
                        ],
                        axis=1,
                    ).max(axis=1)
                    atr14 = float(true_range.rolling(14).mean().iloc[-1])
                    since_entry = completed.loc[
                        [
                            pd.Timestamp(index).date() >= entered_date
                            for index in completed.index
                        ]
                    ]
                    highest_close = float(
                        since_entry['close'].max()
                        if not since_entry.empty
                        else close.iloc[-1]
                    )
                    trailing_stop = highest_close - 3 * atr14

                    if (
                        math.isfinite(trailing_stop)
                        and math.isfinite(ema20)
                    ):
                        rec.stop_loss = max(
                            rec.stop_loss,
                            ema20,
                            trailing_stop,
                        )

                    if float(close.iloc[-1]) < ema20:
                        self._close_swing(
                            rec, current_price, 'ema20_exit', now_kst
                        )
                        continue

                if today_date >= entered_date + datetime.timedelta(
                    days=MAX_HOLD_DAYS
                ):
                    self._close_swing(
                        rec, current_price, 'time_exit', now_kst
                    )
                    continue

                if current_price <= rec.stop_loss:
                    self._close_swing(
                        rec, current_price, 'trailing_stop', now_kst
                    )
                    continue

                rec.save()
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[{rec.coin_ticker}] 스윙 추적 오류: {exc}"
                ))
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
