import time
import pandas as pd
import pyupbit
from django.core.management.base import BaseCommand
from django.utils import timezone
from coinscreener.screener.models import DailyRecommendation

class Command(BaseCommand):
    help = 'KST 오전 9시 기준 전일 거래대금 상위 3개 코인을 선정하고 래리 윌리엄스 돌파 로직(K값 영점조절)을 적용해 추천합니다.'

    def handle(self, *args, **options):
        now_kst = timezone.localtime(timezone.now())
        today_date = now_kst.date()

        # 이미 오늘 자 추천이 있으면 스킵 (중복 방지)
        if DailyRecommendation.objects.filter(date=today_date).exists():
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
                if ema20 <= ema60:
                    self.stdout.write(self.style.WARNING(f"[{today_date}] BTC 1시간봉 EMA20({ema20:.0f}) <= EMA60({ema60:.0f}). 오늘은 단타를 쉬는 날입니다."))
                    return

                # 최근 1시간 하락폭 체크 (직전 종가 대비 현재가 등락률)
                btc_change = (btc_60m['close'].iloc[-1] - btc_60m['close'].iloc[-2]) / btc_60m['close'].iloc[-2] * 100
                if btc_change <= -1.5:
                    self.stdout.write(self.style.WARNING(f"[{today_date}] BTC 최근 1시간 하락폭({btc_change:.2f}%)이 -1.5%를 초과했습니다. 오늘은 단타를 쉬는 날입니다."))
                    return
            else:
                self.stdout.write(self.style.ERROR("BTC 1시간봉 데이터를 가져오지 못했습니다."))
                return

            # 2. BTC 15분봉 RSI > 50 체크
            btc_15m = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=100)
            if btc_15m is not None and len(btc_15m) > 15:
                delta = btc_15m["close"].diff()
                ups = delta.clip(lower=0)
                downs = (-delta).clip(lower=0)
                period = 14
                au = ups.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
                ad = downs.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
                RS = au / ad.replace(0, 1e-10)
                rsi = 100 - (100 / (1 + RS))
                current_rsi = rsi.iloc[-1]

                if current_rsi <= 50:
                    self.stdout.write(self.style.WARNING(f"[{today_date}] BTC 15분봉 RSI({current_rsi:.1f})가 50 이하입니다. 오늘은 단타를 쉬는 날입니다."))
                    return
            else:
                self.stdout.write(self.style.ERROR("BTC 15분봉 데이터를 가져오지 못했습니다."))
                return

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"BTC 필터 확인 중 오류 발생: {e}"))
            return

        self.stdout.write(self.style.SUCCESS("BTC 필터 통과! 종목 스캔을 시작합니다."))

        # 1. 전일 거래대금 상위 코인 찾기 (업비트 원화 마켓 전체 조회)
        tickers = pyupbit.get_tickers(fiat="KRW")
        vol_data = []

        self.stdout.write("코인별 전일 거래대금 스캔 중...")
        # API Rate Limit을 고려하여 일봉 데이터 최신 2개만 가져옴
        for ticker in tickers:
            try:
                df = pyupbit.get_ohlcv(ticker, interval="day", count=2)
                if df is not None and len(df) >= 2:
                    # df.iloc[-2] 가 전일 데이터, df.iloc[-1] 이 당일 데이터(09시 이후 진행중)
                    prev_day = df.iloc[-2]
                    vol_data.append({
                        'ticker': ticker,
                        'value': prev_day['value'] # 거래대금
                    })
            except Exception:
                pass
            time.sleep(0.05) # Rate limit

        # 거래대금 상위 10개 추출
        vol_data.sort(key=lambda x: x['value'], reverse=True)
        top_candidates = [x['ticker'] for x in vol_data[:10]]

        recommendations = []

        # 2. 상위 코인들에 대해 K값 영점 조절 백테스팅 (최근 14일)
        for ticker in top_candidates:
            if len(recommendations) >= 3:
                break
                
            try:
                # 과거 15일 데이터를 불러옴 (마지막은 당일 진행중이므로 뺀 과거 14일을 테스트)
                df = pyupbit.get_ohlcv(ticker, interval="day", count=16)
                if df is None or len(df) < 15:
                    continue

                best_k = 0.5
                best_win_rate = -1.0
                best_profit = -999.0

                # K값 후보군 (0.3 ~ 0.7)
                k_candidates = [0.3, 0.4, 0.5, 0.6, 0.7]
                
                for k in k_candidates:
                    wins = 0
                    trades = 0
                    total_pct = 0.0
                    
                    for i in range(1, len(df) - 1):
                        prev_candle = df.iloc[i-1]
                        curr_candle = df.iloc[i]
                        
                        range_val = prev_candle['high'] - prev_candle['low']
                        entry_price = curr_candle['open'] + range_val * k
                        
                        # 고가가 진입가를 넘었으면 매수 체결됨
                        if curr_candle['high'] >= entry_price:
                            trades += 1
                            target = entry_price * 1.02
                            stop = entry_price * 0.985
                            
                            # 일봉 데이터만으로는 고가/저가 중 어느 것을 먼저 터치했는지 완벽히 알 수 없지만,
                            # 단순화를 위해 고가가 target 이상이면 익절로 간주
                            if curr_candle['high'] >= target:
                                wins += 1
                                total_pct += 2.0
                            elif curr_candle['low'] <= stop:
                                total_pct -= 1.5
                            else:
                                # 종가 마감
                                close_pct = ((curr_candle['close'] - entry_price) / entry_price) * 100
                                total_pct += close_pct
                                if close_pct > 0:
                                    wins += 1
                    
                    win_rate = (wins / trades * 100) if trades > 0 else 0
                    if win_rate > best_win_rate or (win_rate == best_win_rate and total_pct > best_profit):
                        best_win_rate = win_rate
                        best_profit = total_pct
                        best_k = k
                
                # 최적의 K값을 찾았으면, 당일 진입가 계산
                prev_day = df.iloc[-2]
                today = df.iloc[-1]
                
                range_val = prev_day['high'] - prev_day['low']
                entry_price = today['open'] + range_val * best_k
                
                target_price = entry_price * 1.02
                stop_loss = entry_price * 0.985
                
                # 코인 한글명 조회 (없으면 티커 그대로)
                kr_name = ticker.replace('KRW-', '')
                
                reason = f"전일 거래대금 극상위. 최근 14일 벡테스팅 최적화 결과 K={best_k}에서 승률 {best_win_rate:.1f}% 달성. 금일 변동성 돌파 임박."
                
                recommendations.append({
                    'ticker': ticker,
                    'name': kr_name,
                    'entry_price': round(entry_price, 2) if entry_price < 100 else int(entry_price),
                    'target_price': round(target_price, 2) if target_price < 100 else int(target_price),
                    'stop_loss': round(stop_loss, 2) if stop_loss < 100 else int(stop_loss),
                    'k_value': best_k,
                    'reason': reason
                })
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing {ticker}: {e}"))
            time.sleep(0.1)
        
        # 3. DB에 저장
        for rec in recommendations:
            DailyRecommendation.objects.create(
                date=today_date,
                coin_ticker=rec['ticker'],
                coin_name=rec['name'],
                entry_price=rec['entry_price'],
                target_price=rec['target_price'],
                stop_loss=rec['stop_loss'],
                k_value=rec['k_value'],
                reason=rec['reason'],
                status='pending'
            )
            self.stdout.write(self.style.SUCCESS(f"추천 등록 완료: {rec['ticker']} (진입가: {rec['entry_price']})"))
            
        self.stdout.write(self.style.SUCCESS("오늘의 단타 추천 스크립트 실행이 완료되었습니다."))
