import concurrent.futures
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
from django.conf import settings
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

                # 수집이 목표 주기보다 오래 걸려도 즉시 다음 전체 수집을 시작하지
                # 않는다. SQLite·메모리·외부 API에 최소 60초 회복 시간을 준다.
                sleep_time = max(60, 300 - elapsed)
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
        active_timeframes = sorted(set(
            Condition.objects.values_list('timeframe', flat=True).distinct()
        ))
        if not active_timeframes:
            self.stdout.write(
                "활성화된 조건식이 없어 시세 캐시 수집만 생략합니다."
            )
            return

        from coinscreener.screener.engine import indicator_specs_by_timeframe

        specs_by_tf = indicator_specs_by_timeframe(
            list(Condition.objects.all())
        )
        available_sources = (
            ('upbit', lambda: pyupbit.get_tickers(fiat='KRW')),
            ('bithumb', pybithumb.get_tickers),
        )
        sources = tuple(
            source for source in available_sources
            if source[0] in settings.ENABLED_CRYPTO_EXCHANGES
        )
        disabled = [name for name, _ in available_sources if name not in settings.ENABLED_CRYPTO_EXCHANGES]
        if disabled:
            self.stdout.write(f"[CRAWLER_DISABLED_EXCHANGES] {','.join(disabled)}")
        cycle_stats = {
            'requested': 0,
            'success': 0,
            'failed': 0,
            'retried': 0,
        }

        for exchange, load_tickers in sources:
            try:
                tickers = load_tickers()
                if not tickers:
                    raise RuntimeError('종목 목록이 비어 있습니다.')
                tickers = list(tickers)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[CRAWLER_EXCHANGE_ERROR] exchange={exchange} "
                    f"stage=ticker_list error={type(exc).__name__}: {exc}"
                ))
                continue

            self.stdout.write(
                f"[CRAWLER_START] exchange={exchange} "
                f"tickers={len(tickers)} "
                f"timeframes={len(active_timeframes)} "
                f"tasks={len(tickers) * len(active_timeframes)}"
            )
            try:
                stats = self._crawl_exchange(
                    exchange,
                    tickers,
                    active_timeframes,
                    specs_by_tf,
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[CRAWLER_EXCHANGE_ERROR] exchange={exchange} "
                    f"stage=crawl error={type(exc).__name__}: {exc}"
                ))
                continue

            for key in cycle_stats:
                cycle_stats[key] += stats[key]

        self.stdout.write(
            "[CRAWLER_CYCLE_SUMMARY] "
            f"requested={cycle_stats['requested']} "
            f"success={cycle_stats['success']} "
            f"failed={cycle_stats['failed']} "
            f"retried={cycle_stats['retried']}"
        )

    def _crawl_exchange(
        self,
        exchange,
        tickers,
        timeframes,
        specs_by_tf,
    ):
        workers = 6 if exchange == 'upbit' else 12
        tasks = [
            (ticker, timeframe)
            for ticker in tickers
            for timeframe in timeframes
        ]
        started_at = time.monotonic()
        stats = {
            'requested': len(tasks),
            'success': 0,
            'failed': 0,
            'retried': 0,
        }
        error_log_limit = 20

        completed = 0
        batch_size = workers * 4
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f'{exchange}-ohlcv',
        ) as executor:
            for batch_start in range(0, len(tasks), batch_size):
                batch = tasks[batch_start:batch_start + batch_size]
                futures = [
                    executor.submit(
                        self._fetch_only,
                        ticker,
                        timeframe,
                        exchange,
                        specs_by_tf.get(timeframe),
                    )
                    for ticker, timeframe in batch
                ]
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    completed += 1
                    stats['retried'] += max(0, result['attempts'] - 1)
                    if result['df'] is None:
                        stats['failed'] += 1
                        if stats['failed'] <= error_log_limit:
                            self.stdout.write(self.style.ERROR(
                                "[CRAWLER_ITEM_ERROR] "
                                f"exchange={exchange} "
                                f"ticker={result['ticker']} "
                                f"timeframe={result['timeframe']} "
                                f"attempts={result['attempts']} "
                                f"error={result['error']}"
                            ))
                        continue

                    try:
                        self._store_fetch_result(result)
                        stats['success'] += 1
                    except Exception as exc:
                        stats['failed'] += 1
                        if stats['failed'] <= error_log_limit:
                            self.stdout.write(self.style.ERROR(
                                "[CRAWLER_ITEM_ERROR] "
                                f"exchange={exchange} "
                                f"ticker={result['ticker']} "
                                f"timeframe={result['timeframe']} "
                                "stage=save "
                                f"error={type(exc).__name__}: {exc}"
                            ))

                    if completed % 250 == 0:
                        self.stdout.write(
                            f"[CRAWLER_PROGRESS] exchange={exchange} "
                            f"completed={completed}/{len(tasks)} "
                            f"success={stats['success']} "
                            f"failed={stats['failed']}"
                        )

        suppressed = max(0, stats['failed'] - error_log_limit)
        elapsed = time.monotonic() - started_at
        self.stdout.write(
            f"[CRAWLER_EXCHANGE_SUMMARY] exchange={exchange} "
            f"requested={stats['requested']} "
            f"success={stats['success']} "
            f"failed={stats['failed']} "
            f"retried={stats['retried']} "
            f"suppressed_errors={suppressed} "
            f"elapsed={elapsed:.1f}s"
        )
        return stats

    def _fetch_only(
        self,
        ticker,
        timeframe,
        exchange,
        specs,
        retries=3,
    ):
        last_error = 'unknown error'
        for attempt in range(1, retries + 1):
            try:
                df = self._fetch_dataframe(ticker, timeframe, exchange)
                if df is None or df.empty:
                    raise RuntimeError('empty OHLCV response')

                df.index.name = None
                from coinscreener.screener.engine import prewarm_indicators

                prewarm_indicators(df, specs)
                return {
                    'ticker': ticker,
                    'timeframe': timeframe,
                    'exchange': exchange,
                    'df': df,
                    'attempts': attempt,
                    'error': None,
                }
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    time.sleep(0.5 * (2 ** (attempt - 1)))

        return {
            'ticker': ticker,
            'timeframe': timeframe,
            'exchange': exchange,
            'df': None,
            'attempts': retries,
            'error': last_error,
        }

    @staticmethod
    def _fetch_dataframe(ticker, timeframe, exchange):
        from coinscreener.screener.engine import _throttle

        _throttle(exchange)
        if exchange == 'upbit':
            return pyupbit.get_ohlcv(
                ticker,
                interval=timeframe,
                count=200,
            )

        if exchange != 'bithumb':
            raise ValueError(f'unsupported exchange: {exchange}')

        bithumb_tf_map = {
            'minute15': 'minute5',
            'minute30': 'minute30',
            'minute60': 'hour',
            'minute240': 'hour',
            'day': 'day',
            'week': 'day',
            'month': 'day',
        }
        df = pybithumb.get_ohlcv(
            ticker,
            interval=bithumb_tf_map.get(timeframe, 'day'),
        )
        if df is None or df.empty:
            return df

        aggregations = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
        }
        if timeframe == 'minute15':
            df = df.resample('15min').agg(aggregations).dropna()
        elif timeframe == 'minute240':
            df = df.resample('4h').agg(aggregations).dropna()
        elif timeframe == 'week':
            df = df.resample('W-MON').agg(aggregations).dropna()
        elif timeframe == 'month':
            df = df.resample('ME').agg(aggregations).dropna()
        return df.tail(200)

    @staticmethod
    def _store_fetch_result(result):
        from coinscreener.screener.engine import (
            max_cache_age,
            save_ohlcv_cache,
        )

        save_ohlcv_cache(
            result['ticker'],
            result['timeframe'],
            result['df'],
        )
        cache_key = (
            f"ohlcv_{result['ticker']}_{result['timeframe']}_200"
        )
        cache.set(
            cache_key,
            result['df'],
            min(180, max_cache_age(result['timeframe'])),
        )

    def _monitor_recommendations(self):
        """단타와 스윙 추천을 각각의 규칙으로 추적한다."""
        self._monitor_danta_recommendations()
        self._monitor_swing_recommendations()
        self._monitor_paper_positions()

    def _monitor_paper_positions(self):
        """열린 모의 포지션을 1분봉으로 목표·손절 자동 처리한다."""
        from django.utils import timezone
        from coinscreener.screener.models import PaperPosition

        for position in PaperPosition.objects.filter(status='open'):
            try:
                frame = pyupbit.get_ohlcv(
                    position.coin_ticker, interval='minute1', count=1
                )
                if frame is None or frame.empty:
                    continue
                candle = frame.iloc[-1]
                high = float(candle['high'])
                low = float(candle['low'])
                close = float(candle['close'])
                if not all(math.isfinite(value) and value > 0 for value in (high, low, close)):
                    continue
                position.current_price = close
                position.highest_price = max(position.highest_price or high, high)
                position.lowest_price = min(position.lowest_price or low, low)
                # 같은 1분봉에서 목표와 손절이 함께 닿으면 보수적으로 손절 우선.
                if low <= position.stop_loss:
                    position.status = 'closed'
                    position.exit_price = position.stop_loss
                    position.exit_reason = 'stop_loss'
                    position.exit_at = timezone.now()
                elif high >= position.target_price:
                    position.status = 'closed'
                    position.exit_price = position.target_price
                    position.exit_reason = 'target'
                    position.exit_at = timezone.now()
                position.save()
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[{position.coin_ticker}] 모의 포지션 추적 오류: {exc}"
                ))
            time.sleep(0.05)

    def _monitor_danta_recommendations(self):
        from django.utils import timezone
        from coinscreener.screener.models import DailyRecommendation

        today_date = timezone.localtime().date()

        # 전날 이전에 끝났어야 할 단타를 먼저 확정한다. 이전 구현은 오늘 추천만
        # 조회해 과거 pending/active가 성적표에 영구적으로 남았다.
        expired_recs = DailyRecommendation.objects.filter(
            date__lt=today_date,
            trade_type='danta',
            status__in=['pending', 'active', 'partial'],
        ).order_by('date', 'id')
        for rec in expired_recs:
            try:
                candle = self._get_danta_session_candle(rec)
                if candle is None:
                    raise RuntimeError('추천일의 일봉을 찾을 수 없습니다.')
                if self._is_dual_timeframe_danta(rec):
                    self._apply_dual_timeframe_danta_candle(
                        rec, self._danta_session_end(rec.date), candle, finalize=True,
                    )
                else:
                    self._apply_danta_candle(
                        rec, self._danta_session_end(rec.date), candle, finalize=True,
                    )
                rec.save()
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[{rec.coin_ticker}] 지난 단타 마감 처리 오류: {exc}"
                ))
            time.sleep(0.1)

        # 목표 달성 종목도 당일 종료까지 추적해 실제 관측 최고가를 보존한다.
        tracked_recs = DailyRecommendation.objects.filter(
            date=today_date,
            trade_type='danta',
            status__in=['pending', 'active', 'success', 'partial']
        )
        if not tracked_recs.exists():
            return

        self.stdout.write("오늘의 단타 추천 코인 성적 추적 중...")
        for rec in tracked_recs:
            try:
                current_price = float(pyupbit.get_current_price(rec.coin_ticker))
                if not math.isfinite(current_price) or current_price <= 0:
                    continue

                minute_frame = pyupbit.get_ohlcv(
                    rec.coin_ticker, interval='minute1', count=1
                )
                if minute_frame is not None and not minute_frame.empty:
                    candle = minute_frame.iloc[-1]
                    candle_at = self._aware_candle_at(minute_frame.index[-1])
                else:
                    # 일시적으로 1분봉을 받지 못해도 현재가 관측은 보존한다.
                    candle = {
                        'open': current_price,
                        'high': current_price,
                        'low': current_price,
                        'close': current_price,
                    }
                    candle_at = timezone.now()

                if self._is_dual_timeframe_danta(rec):
                    self._apply_dual_timeframe_danta_candle(rec, candle_at, candle)
                else:
                    self._apply_danta_candle(rec, candle_at, candle)

                rec.save()
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"[{rec.coin_ticker}] 단타 추적 오류: {exc}"
                ))
            time.sleep(0.1)

    @staticmethod
    def _is_dual_timeframe_danta(rec):
        return rec.strategy_version == 'danta-1h5m-pullback-v1.0'

    def _close_dual_timeframe_danta(self, rec, exit_price, reason, at):
        rec.exit_price = exit_price
        rec.exit_at = at
        rec.exit_reason = reason
        rec.result_pct = self._swing_result_pct(rec, exit_price)
        rec.status = 'success' if rec.result_pct > 0 else 'failed'

    def _apply_dual_timeframe_danta_candle(self, rec, candle_at, candle, finalize=False):
        """Track TP1 50%, break-even stop, then the 1H Ichimoku exit for dual-TF danta."""
        values = {field: float(candle[field]) for field in ('open', 'high', 'low', 'close')}
        if not all(math.isfinite(value) and value > 0 for value in values.values()):
            raise ValueError('유효하지 않은 듀얼 타임프레임 단타 봉')
        rec.last_checked_at = candle_at
        rec.highest_price = max(rec.highest_price or rec.entry_price, values['high'])
        rec.lowest_price = min(rec.lowest_price or rec.entry_price, values['low'])

        # OHLC 봉 안의 순서를 알 수 없으므로 손절을 먼저 적용한다.
        if values['low'] <= rec.stop_loss:
            exit_price = min(rec.stop_loss, values['open'])
            self._close_dual_timeframe_danta(
                rec, exit_price,
                'break_even_stop' if rec.partial_exit_price else 'stop_loss', candle_at,
            )
            return

        if rec.status == 'active' and values['high'] >= rec.target_price:
            rec.status = 'partial'
            rec.partial_exit_price = rec.target_price
            rec.partial_exit_at = candle_at
            # 수수료를 고려해 TP1 후 손절을 약간의 본절 위로 올린다.
            rec.stop_loss = max(rec.stop_loss, rec.entry_price * 1.001)
            self.stdout.write(self.style.SUCCESS(
                f'[{rec.coin_ticker}] 듀얼 단타 TP1 도달, 50% 익절·본절 이동'
            ))

        if rec.status == 'partial':
            hourly = pyupbit.get_ohlcv(rec.coin_ticker, interval='minute60', count=80)
            if hourly is not None and len(hourly) >= 53:
                completed = hourly.iloc[:-1].astype(float)
                high, low, close = completed['high'], completed['low'], completed['close']
                tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
                kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
                if close.iloc[-1] < kijun.iloc[-1] or tenkan.iloc[-1] < kijun.iloc[-1]:
                    self._close_dual_timeframe_danta(
                        rec, values['close'], 'hourly_ichimoku_exit', candle_at,
                    )
                    return

        if finalize and rec.status in ('active', 'partial'):
            self._close_dual_timeframe_danta(
                rec, values['close'], 'session_close', candle_at,
            )

    @staticmethod
    def _danta_session_end(rec_date):
        """업비트 일봉 경계인 다음 날 KST 09:00을 반환한다."""
        from django.utils import timezone

        naive = datetime.datetime.combine(
            rec_date + datetime.timedelta(days=1),
            datetime.time(hour=9),
        )
        return timezone.make_aware(naive, timezone.get_current_timezone())

    @staticmethod
    def _aware_candle_at(value):
        from django.utils import timezone

        candle_at = pd.Timestamp(value).to_pydatetime()
        if timezone.is_naive(candle_at):
            candle_at = timezone.make_aware(
                candle_at, timezone.get_current_timezone()
            )
        return candle_at

    def _get_danta_session_candle(self, rec):
        """과거 단타 세션을 마감할 추천일 일봉을 가져온다."""
        frame = pyupbit.get_ohlcv(rec.coin_ticker, interval='day', count=200)
        if frame is None or frame.empty:
            return None
        for index, row in frame.iterrows():
            if pd.Timestamp(index).date() == rec.date:
                return row
        return None

    def _apply_danta_candle(self, rec, candle_at, candle, finalize=False):
        """단타 1분봉/마감 일봉을 보수적인 체결 순서로 반영한다."""
        values = {}
        for field in ('open', 'high', 'low', 'close'):
            value = float(candle[field])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"유효하지 않은 단타 봉 {field}: {value}")
            values[field] = value

        candle_open = values['open']
        observed_high = values['high']
        observed_low = values['low']
        was_pending = rec.status == 'pending'
        rec.last_checked_at = candle_at

        if was_pending:
            if observed_high < rec.entry_price:
                if finalize:
                    rec.status = 'closed'
                    rec.exit_reason = 'entry_expired'
                    rec.exit_at = candle_at
                return
            rec.status = 'active'
            rec.entered_at = rec.entered_at or candle_at
            rec.highest_price = max(rec.entry_price, observed_high)
            rec.lowest_price = min(rec.entry_price, observed_low)
            self.stdout.write(self.style.SUCCESS(
                f"[{rec.coin_ticker}] 진입가 도달! (상태: 매수완료)"
            ))

        if rec.status in ('active', 'success'):
            if rec.highest_price is None or observed_high > rec.highest_price:
                rec.highest_price = observed_high
            if rec.lowest_price is None or observed_low < rec.lowest_price:
                rec.lowest_price = observed_low

        if rec.status == 'active':
            # 봉 내부 순서를 알 수 없으면 목표보다 손절을 먼저 적용한다.
            if observed_low <= rec.stop_loss:
                exit_price = (
                    rec.stop_loss
                    if was_pending
                    else min(rec.stop_loss, candle_open)
                )
                rec.status = 'failed'
                rec.exit_price = exit_price
                rec.exit_at = candle_at
                rec.exit_reason = 'stop_loss'
                rec.result_pct = (
                    (exit_price - rec.entry_price) / rec.entry_price * 100
                )
                self.stdout.write(self.style.WARNING(
                    f"[{rec.coin_ticker}] 손절가 이탈... "
                    f"(상태: 손절이탈, 손실: {rec.result_pct:.2f}%)"
                ))
            elif observed_high >= rec.target_price:
                rec.status = 'success'
                rec.exit_price = rec.target_price
                rec.exit_at = candle_at
                rec.exit_reason = 'target'
                rec.result_pct = (
                    (rec.target_price - rec.entry_price)
                    / rec.entry_price
                    * 100
                )
                self.stdout.write(self.style.SUCCESS(
                    f"[{rec.coin_ticker}] 목표가 달성! "
                    f"(상태: 목표달성, 수익: {rec.result_pct:.2f}%)"
                ))

        if finalize and rec.status == 'active':
            rec.status = 'closed'
            rec.exit_price = values['close']
            rec.exit_at = candle_at
            rec.exit_reason = 'session_close'
            rec.result_pct = (
                (rec.exit_price - rec.entry_price) / rec.entry_price * 100
            )

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

    @staticmethod
    def _swing_candle_time(index):
        """Upbit의 KST naive 인덱스를 Django aware datetime으로 변환한다."""
        from django.utils import timezone

        value = pd.Timestamp(index).to_pydatetime()
        if timezone.is_naive(value):
            value = timezone.make_aware(
                value,
                timezone.get_current_timezone(),
            )
        return timezone.localtime(value)

    def _get_missed_swing_candles(self, rec, now_kst):
        """마지막 확인 봉부터 현재 봉까지 가져온다.

        마지막 봉은 의도적으로 다시 포함한다. 직전 조회가 진행 중인 1분봉의
        초반에 이뤄졌더라도 이후 갱신된 고가·저가를 다음 주기에 확인하기 위함이다.
        """
        from django.utils import timezone
        from coinscreener.screener.swing_strategy import MAX_HOLD_DAYS

        start_at = rec.last_checked_at or rec.entered_at or rec.created_at
        start_at = timezone.localtime(start_at)
        oldest_allowed = now_kst - datetime.timedelta(days=MAX_HOLD_DAYS)
        if start_at < oldest_allowed:
            start_at = oldest_allowed

        elapsed_minutes = max(
            1,
            math.ceil((now_kst - start_at).total_seconds() / 60),
        )
        requested_count = elapsed_minutes + 2
        candles = pyupbit.get_ohlcv(
            rec.coin_ticker,
            interval='minute1',
            count=requested_count,
            period=0.12,
        )
        if candles is None or candles.empty:
            raise RuntimeError("누락 1분봉 조회 결과가 없습니다.")

        start_minute = start_at.replace(second=0, microsecond=0)
        rows = []
        candles = candles[
            ~candles.index.duplicated(keep='last')
        ].sort_index()
        for index, candle in candles.iterrows():
            candle_at = self._swing_candle_time(index)
            if candle_at < start_minute or candle_at > now_kst:
                continue
            rows.append((candle_at, candle))

        if not rows:
            raise RuntimeError("마지막 확인 시각 이후의 1분봉이 없습니다.")
        return rows

    @staticmethod
    def _swing_stop_reason(rec):
        if (
            rec.initial_stop_loss is not None
            and rec.stop_loss > rec.initial_stop_loss
        ):
            return 'trailing_stop'
        return 'stop_loss'

    def _apply_swing_candle(self, rec, candle_at, candle):
        """한 개의 1분봉을 기존 보수적 체결 규칙으로 재생한다."""
        values = {}
        for field in ('open', 'high', 'low', 'close'):
            value = float(candle[field])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"유효하지 않은 1분봉 {field}: {value}")
            values[field] = value

        candle_open = values['open']
        observed_high = values['high']
        observed_low = values['low']
        was_pending = rec.status == 'pending'
        rec.last_checked_at = candle_at

        if was_pending:
            if observed_high < rec.entry_price:
                return False
            rec.status = 'active'
            rec.entered_at = candle_at
            rec.highest_price = max(rec.entry_price, observed_high)
            rec.lowest_price = min(rec.entry_price, observed_low)
            self.stdout.write(self.style.SUCCESS(
                f"[{rec.coin_ticker}] 스윙 진입가 도달"
            ))

        if rec.highest_price is None or observed_high > rec.highest_price:
            rec.highest_price = observed_high
        if rec.lowest_price is None or observed_low < rec.lowest_price:
            rec.lowest_price = observed_low

        # 한 봉에서 진입/목표/손절 순서를 알 수 없으면 손절을 우선한다.
        if observed_low <= rec.stop_loss:
            # 봉 시작부터 보유 중이었다면 갭 하락을 시가로 보수적으로 반영한다.
            # 같은 봉에서 처음 진입했다면 진입 전 저가일 수 있어 손절가를 쓴다.
            exit_price = (
                rec.stop_loss
                if was_pending
                else min(rec.stop_loss, candle_open)
            )
            self._close_swing(
                rec,
                exit_price,
                self._swing_stop_reason(rec),
                candle_at,
            )
            return True

        if rec.status == 'active' and observed_high >= rec.target_price:
            rec.status = 'partial'
            rec.partial_exit_price = rec.target_price
            rec.partial_exit_at = candle_at
            self.stdout.write(self.style.SUCCESS(
                f"[{rec.coin_ticker}] 2R 도달, 50% 부분익절"
            ))

        return False

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

                minute_rows = self._get_missed_swing_candles(rec, now_kst)
                current_price = None
                closed = False
                for candle_at, candle in minute_rows:
                    current_price = float(candle['close'])
                    if self._apply_swing_candle(rec, candle_at, candle):
                        closed = True
                        break

                if closed:
                    continue
                if current_price is None:
                    continue

                # 아직 진입되지 않은 신호도 last_checked_at을 저장해 다음 조회량을 제한한다.
                if rec.status == 'pending':
                    rec.save()
                    continue

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
                        rec,
                        current_price,
                        self._swing_stop_reason(rec),
                        now_kst,
                    )
                    continue

                rec.save()
            except Exception as exc:
                # API 실패 시 last_checked_at을 저장하지 않아 다음 주기에 같은 구간을 재시도한다.
                self.stdout.write(self.style.ERROR(
                    f"[{rec.coin_ticker}] 스윙 추적 오류: {exc}"
                ))
            time.sleep(0.1)

