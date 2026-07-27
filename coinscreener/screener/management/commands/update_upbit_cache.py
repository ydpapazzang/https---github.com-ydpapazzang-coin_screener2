import time
import datetime
import pyupbit
import pybithumb
import FinanceDataReader as fdr
import pandas as pd
from django.core.management.base import BaseCommand
from django.core.cache import cache
from coinscreener.screener.models import Condition, OHLCVCache

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
            active_timeframes = set(Condition.objects.values_list('timeframe', flat=True).distinct())
            if not active_timeframes:
                self.stdout.write("활성화된 조건식이 없어 수집을 생략합니다.")
                return

            # 거래소 코인 및 ETF 목록 가져오기
            upbit_tickers = pyupbit.get_tickers(fiat="KRW")
            bithumb_tickers = pybithumb.get_tickers()
            
            # KOSPI (ETF) 가져오기
            try:
                etf_df = fdr.StockListing('ETF/KR')
                etf_code_col = 'Symbol' if 'Symbol' in etf_df.columns else 'Code'
                kospi_tickers = etf_df[etf_code_col].astype(str).tolist()
            except Exception as e:
                self.stdout.write(f"ETF 목록 가져오기 실패: {e}")
                kospi_tickers = []
            
            self.stdout.write(f"업비트 {len(upbit_tickers)}개, 빗썸 {len(bithumb_tickers)}개, ETF {len(kospi_tickers)}개, {len(active_timeframes)}개 타임프레임 수집 시작...")

            # 업비트 수집
            for ticker in upbit_tickers:
                for tf in active_timeframes:
                    self._fetch_and_cache(ticker, tf, exchange='upbit')

            # 빗썸 수집
            for ticker in bithumb_tickers:
                for tf in active_timeframes:
                    self._fetch_and_cache(ticker, tf, exchange='bithumb')

            # KOSPI 수집
            for ticker in kospi_tickers:
                for tf in active_timeframes:
                    self._fetch_and_cache(ticker, tf, exchange='kospi')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during crawl cycle: {e}"))

    def _fetch_and_cache(self, ticker, timeframe, exchange='upbit'):
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
                
                data_dict = {
                    'index': df.index.view('int64').tolist(),
                    'columns': df.columns.tolist(),
                    'data': df.values.tolist(),
                }
                
                OHLCVCache.objects.update_or_create(
                    ticker=ticker,
                    timeframe=timeframe,
                    defaults={'data': data_dict}
                )
                
                cache_key = f"ohlcv_{ticker}_{timeframe}_200"
                cache.set(cache_key, df, 180)
                
        except Exception as e:
            pass
