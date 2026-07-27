import time
import datetime
import FinanceDataReader as fdr
import pandas as pd
from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.utils import timezone
from coinscreener.screener.models import Condition, OHLCVCache

class Command(BaseCommand):
    help = 'KOSPI(ETF) 데이터를 수집하여 캐시에 저장합니다. 평일 09:00~15:30 동안 1시간마다 작동합니다.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting KOSPI/ETF Cache Bot..."))
        
        while True:
            # 한국 시간(KST) 구하기
            now_kst = timezone.localtime()
            
            is_weekday = now_kst.weekday() < 5 # 0: 월 ~ 4: 금
            hour = now_kst.hour
            minute = now_kst.minute
            
            # 09:00 ~ 15:30 장 중인지 확인
            is_market_open = is_weekday and (
                (hour >= 9 and hour < 15) or
                (hour == 15 and minute <= 30)
            )

            if is_market_open:
                self.stdout.write(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] KOSPI Market is OPEN. Starting crawl cycle...")
                start_time = time.time()
                self._run_crawler()
                elapsed = time.time() - start_time
                
                # 3600초(1시간) 주기
                sleep_time = max(0, 3600 - elapsed)
                self.stdout.write(f"Cycle finished in {elapsed:.1f}s. Sleeping for {sleep_time:.1f}s until next hour...")
                time.sleep(sleep_time)
            else:
                self.stdout.write(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] KOSPI Market is CLOSED. Sleeping for 10 minutes...")
                time.sleep(600) # 10분 대기 후 다시 시간 체크

    def _run_crawler(self):
        try:
            active_timeframes = set(Condition.objects.values_list('timeframe', flat=True).distinct())
            if not active_timeframes:
                self.stdout.write("활성화된 조건식이 없어 수집을 생략합니다.")
                return

            try:
                etf_df = fdr.StockListing('ETF/KR')
                etf_code_col = 'Symbol' if 'Symbol' in etf_df.columns else 'Code'
                kospi_tickers = etf_df[etf_code_col].astype(str).tolist()
            except Exception as e:
                self.stdout.write(f"ETF 목록 가져오기 실패: {e}")
                kospi_tickers = []
            
            self.stdout.write(f"ETF {len(kospi_tickers)}개, {len(active_timeframes)}개 타임프레임 수집 시작...")

            for ticker in kospi_tickers:
                for tf in active_timeframes:
                    self._fetch_and_cache(ticker, tf)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during crawl cycle: {e}"))

    def _fetch_and_cache(self, ticker, timeframe):
        time.sleep(0.10) # API Rate limit (네이버 금융은 0.1초면 충분)
        try:
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
