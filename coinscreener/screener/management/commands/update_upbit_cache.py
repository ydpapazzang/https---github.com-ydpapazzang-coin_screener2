import time
import datetime
import pyupbit
import pandas as pd
from django.core.management.base import BaseCommand
from django.core.cache import cache
from coinscreener.screener.models import Condition, OHLCVCache, MarketData

class Command(BaseCommand):
    help = '1분마다 조건식에 사용된 타임프레임의 데이터를 백그라운드에서 수집하여 캐시에 저장합니다.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting 24/7 Upbit Cache Bot..."))
        
        while True:
            start_time = time.time()
            self._run_crawler()
            elapsed = time.time() - start_time
            
            # 60초 주기 (수집에 걸린 시간을 빼고 휴식)
            sleep_time = max(0, 60 - elapsed)
            self.stdout.write(f"Cycle finished in {elapsed:.1f}s. Sleeping for {sleep_time:.1f}s...")
            time.sleep(sleep_time)

    def _run_crawler(self):
        try:
            # 1. DB에 저장된 조건식들에서 사용 중인 타임프레임만 추출 (불필요한 수집 방지)
            active_timeframes = set(Condition.objects.values_list('timeframe', flat=True).distinct())
            if not active_timeframes:
                self.stdout.write("활성화된 조건식이 없어 수집을 생략합니다.")
                return

            # 2. 거래대금 상위 또는 전체 코인 목록 가져오기 (우선 전체)
            tickers = pyupbit.get_tickers(fiat="KRW")
            self.stdout.write(f"총 {len(tickers)}개 코인, {len(active_timeframes)}개 타임프레임 수집 시작...")

            for ticker in tickers:
                for tf in active_timeframes:
                    self._fetch_and_cache(ticker, tf)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during crawl cycle: {e}"))

    def _fetch_and_cache(self, ticker, timeframe):
        # API Rate Limit (초당 10회) 엄수
        time.sleep(0.12)
        
        try:
            # 기본적으로 200개 (최대) 가져오기
            df = pyupbit.get_ohlcv(ticker, interval=timeframe, count=200)
            if df is not None and not df.empty:
                df.index.name = None
                
                # DB 저장용 데이터 (JSON 직렬화)
                data_dict = {
                    'index': df.index.view('int64').tolist(),
                    'columns': df.columns.tolist(),
                    'data': df.values.tolist(),
                }
                
                # DB 캐시 업데이트
                OHLCVCache.objects.update_or_create(
                    ticker=ticker,
                    timeframe=timeframe,
                    defaults={'data': data_dict}
                )
                
                # 메모리 캐시 (180초 유지)
                cache_key = f"ohlcv_{ticker}_{timeframe}_200"
                cache.set(cache_key, df, 180)
                
        except Exception as e:
            # Rate limit 등 에러 시 조용히 넘어감
            pass
