import math
import pyupbit
import pandas as pd
import time
import numpy as np
import threading
import random
import datetime
import FinanceDataReader as fdr
from django.core.cache import cache
import logging
import traceback

logger = logging.getLogger(__name__)

def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return 0.0


_rate_limit_lock = threading.Lock()
_last_request_time = 0.0
_min_interval = 0.12  # 최소 0.12초 간격 (초당 최대 ~8.3회 요청)


def get_ohlcv_with_retry(ticker, interval, count=400, retries=5, delay=0.4):
    """API 호출 제한을 고려하여 글로벌 속도 제한 및 지터 재시도가 적용된 OHLCV 조회"""
    # 1. 캐시 확인 (5분 타임아웃, 같은 종목/타임프레임 재요청 시 즉시 반환)
    cache_key = f"ohlcv_{ticker}_{interval}_{count}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    # 1.5. DB 사전 캐시 확인 (Pre-fetching)
    try:
        from .models import OHLCVCache
        import json
        cached_obj = OHLCVCache.objects.filter(ticker=ticker, timeframe=interval).first()
        if cached_obj and cached_obj.data:
            # Check if it's too old (e.g., > 10 minutes)
            # Since Vercel Cron might not run, we want fresh data.
            # But if we rely on cron-job.org, it's 1 minute.
            # Let's accept up to 15 minutes old data as "fresh enough" for a fallback.
            # API 폴백이 차단되었으므로 캐시 유효기간을 넉넉히(7일) 잡아 무조건 캐시를 활용하도록 합니다.
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - cached_obj.updated_at).total_seconds() < 604800:
                json_str = json.dumps(cached_obj.data)
                import io
                df = pd.read_json(io.StringIO(json_str), orient='split')
                df.index.name = None
                df_tail = df.tail(count)
                if len(df_tail) >= 300:  # 데이터가 충분한 경우에만 캐시 허용
                    cache.set(cache_key, df_tail, 180)
                    return df_tail
    except Exception as e:
        logger.error(f"OHLCVCache read error for {ticker}: {e}", exc_info=True)

    # 캐시가 없거나 부족한 경우 (ex: 200개 이하), 예외적으로 실시간 조회를 허용하여 누락을 방지합니다.
    try:
        import time
        import pyupbit
        for attempt in range(retries):
            if ticker.startswith('KRW-'):
                df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
            elif '_' in ticker: # Bithumb
                import pybithumb
                bithumb_tf_map = {'minute15': 'minute5', 'minute30': 'minute30', 'minute60': 'hour', 'minute240': 'hour', 'day': 'day', 'week': 'day', 'month': 'day'}
                btf = bithumb_tf_map.get(interval, 'day')
                df = pybithumb.get_ohlcv(ticker, interval=btf)
                if df is not None and not df.empty:
                    if interval == 'minute15': df = df.resample('15min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif interval == 'minute240': df = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif interval == 'week': df = df.resample('W-MON').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif interval == 'month': df = df.resample('ME').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    df = df.tail(count)
            else: # KOSPI
                import FinanceDataReader as fdr
                df = fdr.DataReader(ticker)
                if df is not None and not df.empty:
                    df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
                    if interval == 'week': df = df.resample('W-MON').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    elif interval == 'month': df = df.resample('ME').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
                    df = df.tail(count)
                    
            if df is not None and not df.empty:
                df.index.name = None
                cache.set(cache_key, df, 180)
                return df
            time.sleep(delay)
    except Exception as e:
        logger.error(f"Live API fallback error for {ticker}: {e}")
        
    return None


def calculate_rsi(ohlc: pd.DataFrame, period: int = 14):
    """
    Wilder의 지수이동평균 방식으로 RSI 계산.
    alpha=1/period 를 사용해 실제 14-period RSI에 정확히 대응.
    (기존 com=period-1 방식은 span=2*period-1에 해당하여 느리게 수렴하는 문제 있었음)
    """
    delta = ohlc["close"].diff()
    ups = delta.clip(lower=0)
    downs = (-delta).clip(lower=0)

    au = ups.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    ad = downs.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # ad가 0인 경우(하락이 전혀 없는 구간) ZeroDivisionError 방지
    RS = au / ad.replace(0, float('nan'))
    rsi = 100 - (100 / (1 + RS))
    return pd.Series(rsi, name="RSI")


def calculate_wma(series: pd.Series, period: int):
    """가중이동평균(WMA) 계산"""
    if len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)
    weights = np.arange(1, period + 1)
    wma = series.rolling(window=period).apply(lambda prices: np.dot(prices, weights) / weights.sum(), raw=True)
    return wma


def calculate_bollinger(series: pd.Series, period: int = 20, std: float = 2.0):
    """볼린저 밴드 계산. 상단, 중간, 하단 밴드를 포함하는 DataFrame 반환"""
    ma = series.rolling(window=period).mean()
    mstd = series.rolling(window=period).std()
    upper = ma + (mstd * std)
    lower = ma - (mstd * std)
    return pd.DataFrame({'BB_UPPER': upper, 'BB_MIDDLE': ma, 'BB_LOWER': lower}, index=series.index)




def calculate_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    하이킨아시 캔들 계산.
    HA_close  = (open + high + low + close) / 4
    HA_open   = (prev_HA_open + prev_HA_close) / 2  (첫 봉은 (open+close)/2)
    HA_high   = max(high, HA_open, HA_close)
    HA_low    = min(low,  HA_open, HA_close)
    """
    ha_close = (df['open'] + df['high'] + df['low'] + df['close']) / 4

    ha_open = ha_close.copy()
    ha_open.iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2

    ha_high = pd.concat([df['high'], ha_open, ha_close], axis=1).max(axis=1)
    ha_low  = pd.concat([df['low'],  ha_open, ha_close], axis=1).min(axis=1)

    return pd.DataFrame({
        'ha_open':  ha_open,
        'ha_high':  ha_high,
        'ha_low':   ha_low,
        'ha_close': ha_close,
    }, index=df.index)


def check_ha_pattern(df: pd.DataFrame, pattern: str, param: int, offset: int) -> bool:
    """
    하이킨아시 패턴 충족 여부 반환.
    pattern: HA_BULL / HA_BEAR / HA_BULL_N / HA_BEAR_N / HA_NO_LOWER / HA_NO_UPPER
    param  : HA_BULL_N / HA_BEAR_N 의 연속 N봉 수
    offset : 0 = 현재봉, 1 = 1봉 전 ...
    """
    ha = calculate_heikin_ashi(df)
    target = -1 - offset

    if abs(target) > len(ha):
        return False

    if pattern == 'HA_BULL':
        return float(ha['ha_close'].iloc[target]) >= float(ha['ha_open'].iloc[target])

    if pattern == 'HA_BEAR':
        return float(ha['ha_close'].iloc[target]) < float(ha['ha_open'].iloc[target])

    if pattern == 'HA_BULL_N':
        n = max(1, param)
        if len(ha) < n + abs(offset):
            return False
        for i in range(n):
            idx = target - i
            if abs(idx) > len(ha):
                return False
            if float(ha['ha_close'].iloc[idx]) < float(ha['ha_open'].iloc[idx]):
                return False
        return True

    if pattern == 'HA_BEAR_N':
        n = max(1, param)
        if len(ha) < n + abs(offset):
            return False
        for i in range(n):
            idx = target - i
            if abs(idx) > len(ha):
                return False
            if float(ha['ha_close'].iloc[idx]) >= float(ha['ha_open'].iloc[idx]):
                return False
        return True

    if pattern == 'HA_NO_LOWER':
        # 아랫꼬리 없음: HA저가 ≈ HA시가 (오차 0.01% 허용)
        ha_o = float(ha['ha_open'].iloc[target])
        ha_l = float(ha['ha_low'].iloc[target])
        return abs(ha_l - ha_o) / (ha_o + 1e-10) < 0.0001

    if pattern == 'HA_NO_UPPER':
        # 윗꼬리 없음: HA고가 ≈ HA종가 (오차 0.01% 허용)
        ha_c = float(ha['ha_close'].iloc[target])
        ha_h = float(ha['ha_high'].iloc[target])
        return abs(ha_h - ha_c) / (ha_c + 1e-10) < 0.0001

    return False


def get_required_len(indicator_type, param):
    """지표 계산에 필요한 최소 데이터 길이 반환"""
    if indicator_type in ('VAL', 'CLOSE', 'VOLUME', 'CHANGE_RATE'):
        return 0
    if indicator_type == 'VOLUME_PREV':
        return 1
    if indicator_type == 'VOLUME_MA':
        return param
    if indicator_type == 'IC_TENKAN':
        return param
    if indicator_type == 'IC_KIJUN':
        return param
    if indicator_type == 'IC_SPAN_A':
        return 52
    if indicator_type == 'IC_SPAN_B':
        return 78
    if indicator_type in ('IC_CLOUD_TOP', 'IC_CLOUD_BOTTOM'):
        return 78
    if indicator_type == 'IC_CHIKOU':
        return 0
    if indicator_type == 'IC_CHIKOU_REF':
        return param
    return param


def check_strategy(ticker, conditions, current_price=None):
    """
    특정 코인이 주어진 전략(조건 리스트)을 모두 만족하는지 확인.
    조건이 비어있으면 매칭하지 않음. 
    
    Returns:
        (is_match, details, last_price, volume, status)
        status: 'new', 'maintained', or None
    """
    # 조건이 없으면 모든 코인이 통과되는 버그 방지
    if not conditions:
        return False, [], None, 0, 0.0, None

    try:
        data_cache = {}
        details = []
        last_price = None
        volume = 0
        change_rate = 0.0

        def _check_for_offset(base_offset):
            """Helper to check all conditions for a given base offset."""
            for cond in conditions:
                if cond.timeframe not in data_cache:
                    df = get_ohlcv_with_retry(ticker, interval=cond.timeframe)
                    if df is None: return False
                    data_cache[cond.timeframe] = df
                
                df = data_cache[cond.timeframe]
                
                cond_met = False
                # cond.offset이 'n봉 이내'를 의미하므로, 0부터 cond.offset까지 모든 봉을 검사하여 하나라도 만족하면 True
                for i in range(cond.offset + 1):
                    total_offset = base_offset + i
                    
                    required_len = max(
                        get_required_len(cond.left_indicator, cond.left_param),
                        get_required_len(cond.right_indicator, cond.right_param)
                    ) + total_offset + 2  # +2 for cross_up/down

                    if len(df) < required_len:
                        continue
                        
                    # ── 하이킨아시 패턴 조건 ──
                    ha_patterns = ('HA_BULL','HA_BEAR','HA_BULL_N','HA_BEAR_N','HA_NO_LOWER','HA_NO_UPPER')
                    if cond.left_indicator in ha_patterns:
                        if check_ha_pattern(df, cond.left_indicator, cond.left_param, total_offset):
                            cond_met = True
                            break
                        continue
                        
                    bb_std = cond.bb_std if cond.bb_std is not None else 2.0
                    left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, total_offset, bb_std=bb_std)
                    right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, total_offset, bb_std=bb_std)

                    if left_val is None or right_val is None or pd.isna(left_val) or pd.isna(right_val):
                        continue

                    if cond.operator == 'btw':
                        if cond.left_indicator == 'VOLUME':
                            max_multiplier = cond.left_param / 100.0
                            max_val = get_indicator_value(df, cond.right_indicator, cond.right_param, total_offset, bb_std=max_multiplier)
                        else:
                            max_val = cond.bb_std if cond.bb_std is not None else float('inf')
                        
                        if (right_val <= left_val <= max_val):
                            cond_met = True
                            break
                    elif cond.operator in ('cross_up', 'cross_down'):
                        prev_offset = total_offset + 1
                        left_val_prev = get_indicator_value(df, cond.left_indicator, cond.left_param, prev_offset, bb_std=bb_std)
                        right_val_prev = get_indicator_value(df, cond.right_indicator, cond.right_param, prev_offset, bb_std=bb_std)
                        
                        if left_val_prev is None or right_val_prev is None or pd.isna(left_val_prev) or pd.isna(right_val_prev):
                            continue
                            
                        if cond.operator == 'cross_up':
                            if left_val_prev <= right_val_prev and left_val > right_val:
                                cond_met = True
                                break
                        elif cond.operator == 'cross_down':
                            if left_val_prev >= right_val_prev and left_val < right_val:
                                cond_met = True
                                break
                    else:
                        op_map = {'gt': left_val > right_val, 'lt': left_val < right_val, 'gte': left_val >= right_val, 'lte': left_val <= right_val}
                        if op_map.get(cond.operator):
                            cond_met = True
                            break
                            
                if not cond_met:
                    return False
            return True

        # 1. 현재 봉(base_offset=0) 조건 확인
        is_match_current = _check_for_offset(0)

        # 현재가와 거래대금은 데이터가 있으면 항상 가져옴
        if conditions and conditions[0].timeframe in data_cache:
            df = data_cache[conditions[0].timeframe]
            if not df.empty:
                last_price = df['close'].iloc[-1]
                volume = df['value'].iloc[-1] if 'value' in df.columns else df['volume'].iloc[-1]
                
        # 등락률(change_rate) 계산을 위해 'day' 데이터 강제 로드
        day_df = data_cache.get('day')
        if day_df is None:
            day_df = get_ohlcv_with_retry(ticker, interval='day')
            if day_df is not None:
                data_cache['day'] = day_df

        if day_df is not None and len(day_df) > 1:
            current_close = day_df['close'].iloc[-1]
            prev_close = day_df['close'].iloc[-2]
            if prev_close > 0:
                change_rate = (current_close - prev_close) / prev_close * 100.0

        if not is_match_current:
            return False, [], last_price, volume, change_rate, None

        # 2. 이전 봉(base_offset=1) 조건 확인
        is_match_previous = _check_for_offset(1)
        status = 'maintained' if is_match_previous else 'new'

        # 3. 현재 봉 기준 details 수집
        ha_patterns = ('HA_BULL','HA_BEAR','HA_BULL_N','HA_BEAR_N','HA_NO_LOWER','HA_NO_UPPER')
        HA_LABEL = {
            'HA_BULL': 'HA 양봉', 'HA_BEAR': 'HA 음봉',
            'HA_BULL_N': f'HA 연속 양봉', 'HA_BEAR_N': f'HA 연속 음봉',
            'HA_NO_LOWER': 'HA 아랫꼬리 없음', 'HA_NO_UPPER': 'HA 윗꼬리 없음',
        }
        for cond in conditions:
            df = data_cache[cond.timeframe]
            if cond.left_indicator in ha_patterns:
                label = HA_LABEL.get(cond.left_indicator, cond.left_indicator)
                if 'N' in cond.left_indicator:
                    label = label.replace('연속', f'연속{cond.left_param}봉')
                details.append(label)
                continue
            bb_std = cond.bb_std if cond.bb_std is not None else 2.0
            left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, cond.offset, bb_std=bb_std)
            right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, cond.offset, bb_std=bb_std)

            if cond.operator == 'btw':
                if cond.left_indicator == 'VOLUME':
                    max_multiplier = cond.left_param / 100.0
                    max_val = get_indicator_value(df, cond.right_indicator, cond.right_param, cond.offset, bb_std=max_multiplier)
                    details.append(f"거래량: {left_val:,.0f} (범위: {right_val:,.0f} ~ {max_val:,.0f})")
                else:
                    max_val = cond.bb_std if cond.bb_std is not None else float('inf')
                    details.append(f"{cond.left_indicator}({cond.left_param}): {left_val:.2f} (범위: {right_val:.2f} ~ {max_val:.2f})")
            else:
                if cond.left_indicator != 'VAL':
                    if left_val is not None and not pd.isna(left_val):
                        if cond.left_indicator == 'VOLUME':
                            details.append(f"거래량: {left_val:,.0f}")
                        else:
                            details.append(f"{cond.left_indicator}({cond.left_param}): {left_val:.2f}")
                if cond.right_indicator != 'VAL':
                    if right_val is not None and not pd.isna(right_val):
                        if 'VOLUME' in cond.right_indicator:
                            details.append(f"{cond.right_indicator}: {right_val:,.0f}")
                        else:
                            details.append(f"{cond.right_indicator}({cond.right_param}): {right_val:.2f}")

        return True, details, _safe_float(last_price), _safe_float(volume), _safe_float(change_rate), status

    except Exception as e:
        logger.error(f"[Engine] Exception in check_strategy: {e}", exc_info=True)
        return False, [], None, 0, 0.0, None


def get_indicator_value(df, indicator_type, param, offset, bb_std=2.0):
    """
    DataFrame에서 특정 시점(offset)의 지표값을 반환.
    O(1) 캐싱을 적용하여 백테스팅 시 반복 연산을 최소화.
    """
    target_idx = -1 - offset
    if abs(target_idx) > len(df):
        return None

    if indicator_type == 'VAL':
        return float(param)

    if indicator_type == 'CLOSE':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)
        
    if indicator_type == 'VOLUME':
        val = df['volume'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    if indicator_type == 'CHANGE_RATE':
        prev_idx = target_idx - 1
        if abs(prev_idx) > len(df) or len(df) < 2:
            return None
        prev_close = df['close'].iloc[prev_idx]
        curr_close = df['close'].iloc[target_idx]
        if prev_close == 0:
            return None
        return float((curr_close - prev_close) / prev_close * 100)
        
    if indicator_type == 'VOLUME_PREV':
        prev_idx = target_idx - 1
        if abs(prev_idx) > len(df): return None
        val = df['volume'].iloc[prev_idx]
        multiplier = bb_std if bb_std is not None else 1.0
        val = val * multiplier
        return None if pd.isna(val) else float(val)

    if indicator_type == 'VOLUME_MA':
        if param < 1: return None
        start_idx = target_idx - param
        end_idx = target_idx
        if abs(start_idx) > len(df): return None
        vol_slice = df['volume'].iloc[start_idx : end_idx]
        if vol_slice.empty: return None
        avg_val = vol_slice.mean()
        multiplier = bb_std if bb_std is not None else 1.0
        val = avg_val * multiplier
        return None if pd.isna(val) else float(val)

    # O(1) DataFrame 컬럼 캐싱
    col_name = f"{indicator_type}_{param}_{bb_std}"
    
    if col_name not in df.columns:
        if indicator_type == 'MA':
            if param < 1: return None
            df[col_name] = df['close'].rolling(window=param).mean()

        elif indicator_type == 'EMA':
            if param < 1: return None
            df[col_name] = df['close'].ewm(span=param, adjust=False).mean()

        elif indicator_type == 'WMA':
            if param < 1: return None
            df[col_name] = calculate_wma(df['close'], period=param)

        elif indicator_type == 'RSI':
            if param < 1: return None
            df[col_name] = calculate_rsi(df, period=param)

        elif indicator_type in ('BB_UPPER', 'BB_MIDDLE', 'BB_LOWER'):
            if param < 1: return None
            std = bb_std if bb_std is not None else 2.0
            bb = calculate_bollinger(df['close'], period=param, std=std)
            df['BB_UPPER_{}_{}'.format(param, bb_std)] = bb['BB_UPPER']
            df['BB_MIDDLE_{}_{}'.format(param, bb_std)] = bb['BB_MIDDLE']
            df['BB_LOWER_{}_{}'.format(param, bb_std)] = bb['BB_LOWER']
            if col_name not in df.columns:
                return None

        elif indicator_type == 'IC_TENKAN':
            p = max(1, param)
            df[col_name] = (df['high'].rolling(window=p).max() + df['low'].rolling(window=p).min()) / 2

        elif indicator_type == 'IC_KIJUN':
            p = max(1, param)
            df[col_name] = (df['high'].rolling(window=p).max() + df['low'].rolling(window=p).min()) / 2

        elif indicator_type == 'IC_SPAN_A':
            tenkan = (df['high'].rolling(window=9).max() + df['low'].rolling(window=9).min()) / 2
            kijun = (df['high'].rolling(window=26).max() + df['low'].rolling(window=26).min()) / 2
            span_a = (tenkan + kijun) / 2
            df[col_name] = span_a.shift(param)

        elif indicator_type == 'IC_SPAN_B':
            span_b = (df['high'].rolling(window=52).max() + df['low'].rolling(window=52).min()) / 2
            df[col_name] = span_b.shift(param)

        elif indicator_type in ('IC_CLOUD_TOP', 'IC_CLOUD_BOTTOM'):
            tenkan = (df['high'].rolling(window=9).max() + df['low'].rolling(window=9).min()) / 2
            kijun = (df['high'].rolling(window=26).max() + df['low'].rolling(window=26).min()) / 2
            span_a = (tenkan + kijun) / 2
            span_a_shifted = span_a.shift(param)

            span_b = (df['high'].rolling(window=52).max() + df['low'].rolling(window=52).min()) / 2
            span_b_shifted = span_b.shift(param)
            
            df['IC_CLOUD_TOP_{}_{}'.format(param, bb_std)] = np.maximum(span_a_shifted, span_b_shifted)
            df['IC_CLOUD_BOTTOM_{}_{}'.format(param, bb_std)] = np.minimum(span_a_shifted, span_b_shifted)

        elif indicator_type == 'IC_CHIKOU':
            df[col_name] = df['close']

        elif indicator_type == 'IC_CHIKOU_REF':
            df[col_name] = df['close'].shift(param)
            
        else:
            return None

    val = df[col_name].iloc[target_idx]
    return None if pd.isna(val) else float(val)