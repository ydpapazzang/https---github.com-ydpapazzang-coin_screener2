import time
import json
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



            # 단타 추천 코인 실시간 성적 추적
            self._monitor_recommendations()

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during crawl cycle: {e}"))

    def _monitor_recommendations(self):
        from django.utils import timezone
        from coinscreener.screener.models import DailyRecommendation
        today_date = timezone.localtime().date()
        
        # 오늘자 추천 코인 중 아직 완료되지 않은 것들 추적
        active_recs = DailyRecommendation.objects.filter(
            date=today_date,
            status__in=['pending', 'active']
        )
        if not active_recs.exists():
            return
            
        self.stdout.write("오늘의 단타 추천 코인 성적 추적 중...")
        for rec in active_recs:
            try:
                # 현재가 조회
                current_price = pyupbit.get_current_price(rec.coin_ticker)
                if not current_price:
                    continue
                    
                # 최고가/최저가 업데이트
                if rec.highest_price is None or current_price > rec.highest_price:
                    rec.highest_price = current_price
                if rec.lowest_price is None or current_price < rec.lowest_price:
                    rec.lowest_price = current_price
                    
                # 상태 업데이트 로직
                if rec.status == 'pending':
                    if current_price >= rec.entry_price:
                        rec.status = 'active'
                        self.stdout.write(self.style.SUCCESS(f"[{rec.coin_ticker}] 진입가 도달! (상태: 매수완료)"))
                
                if rec.status == 'active':
                    if current_price >= rec.target_price:
                        rec.status = 'success'
                        rec.result_pct = ((rec.target_price - rec.entry_price) / rec.entry_price) * 100
                        self.stdout.write(self.style.SUCCESS(f"[{rec.coin_ticker}] 목표가 달성! (상태: 목표달성, 수익: {rec.result_pct:.2f}%)"))
                    elif current_price <= rec.stop_loss:
                        rec.status = 'failed'
                        rec.result_pct = ((rec.stop_loss - rec.entry_price) / rec.entry_price) * 100
                        self.stdout.write(self.style.WARNING(f"[{rec.coin_ticker}] 손절가 이탈... (상태: 손절이탈, 손실: {rec.result_pct:.2f}%)"))
                        
                rec.save()
            except Exception as e:
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
                from coinscreener.screener.engine import prewarm_indicators
                prewarm_indicators(df, specs)

                # 지표 선행구간 NaN이 있어도 유효 JSON이 되도록 to_json(NaN->null) 사용.
                # df.values.tolist()는 NaN 토큰을 만들어 SQLite JSON_VALID 제약에 걸린다.
                data_dict = json.loads(df.to_json(orient='split'))

                OHLCVCache.objects.update_or_create(
                    ticker=ticker,
                    timeframe=timeframe,
                    defaults={'data': data_dict}
                )

                cache_key = f"ohlcv_{ticker}_{timeframe}_200"
                cache.set(cache_key, df, 180)
                
        except Exception as e:
            pass
