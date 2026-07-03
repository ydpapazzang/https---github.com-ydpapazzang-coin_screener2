import pyupbit
import pandas as pd
import time
import numpy as np
import threading
import random
import datetime
import FinanceDataReader as fdr

_rate_limit_lock = threading.Lock()
_last_request_time = 0.0
_min_interval = 0.12  # 최소 0.12초 간격 (초당 최대 ~8.3회 요청)


def get_ohlcv_with_retry(ticker, interval, count=200, retries=5, delay=0.4):
    """API 호출 제한을 고려하여 글로벌 속도 제한 및 지터 재시도가 적용된 OHLCV 조회"""
    global _last_request_time
    
    for i in range(retries):
        # 글로벌 요청 간격 조절 (최소 0.12초 간격 보장)
        with _rate_limit_lock:
            now = time.time()
            elapsed = now - _last_request_time
            if elapsed < _min_interval:
                sleep_time = _min_interval - elapsed
            else:
                sleep_time = 0.0
            _last_request_time = now + sleep_time
            
        if sleep_time > 0:
            time.sleep(sleep_time)
            
        try:
            if ticker.isdigit() and len(ticker) == 6:
                # KOSPI / ETF 주식 처리
                days_to_fetch = count * 2 if interval == 'day' else (count * 8 if interval == 'week' else count * 35)
                start_date = (datetime.datetime.now() - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
                df = fdr.DataReader(ticker, start_date)
                
                if df is not None:
                    if df.empty:
                        return None # 빈 데이터는 재시도 없이 즉시 반환
                        
                    df = df.rename(columns={
                        'Open': 'open', 'High': 'high', 'Low': 'low', 
                        'Close': 'close', 'Volume': 'volume', 'Change': 'change'
                    })
                    
                    if interval == 'week':
                        logic = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
                        df = df.resample('W').apply(logic).dropna()
                    elif interval == 'month':
                        logic = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
                        # 'ME' is Month End (standard in newer pandas)
                        df = df.resample('ME').apply(logic).dropna()
                        
                    return df.tail(count)
            else:
                # 기존 코인 처리
                df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
                if df is not None:
                    return df
        except Exception as e:
            print(f"get_ohlcv error for {ticker}: {e}")
            
        # 실패 시 랜덤 지터가 포함된 점진적 백오프 후 재시도
        time.sleep(delay * (i + 1) + random.uniform(0.1, 0.3))
        
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
    if indicator_type in ('VAL', 'CLOSE', 'VOLUME'):
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
        return False, [], None, 0, None

    try:
        data_cache = {}
        details = []
        last_price = None
        volume = 0

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

        if not is_match_current:
            return False, [], last_price, volume, None

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
        
        return True, details, last_price, volume, status

    except Exception as e:
        print(f"Error checking {ticker}: {e}")
        return False, [], None, 0, None


def get_indicator_value(df, indicator_type, param, offset, bb_std=2.0):
    """
    DataFrame에서 특정 시점(offset)의 지표값을 반환.
    offset=0: 가장 최근 봉, offset=1: 1봉 전
    """
    target_idx = -1 - offset

    if abs(target_idx) > len(df):
        return None

    if indicator_type == 'MA':
        if param < 1: return None
        ma = df['close'].rolling(window=param).mean()
        val = ma.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'EMA':
        if param < 1: return None
        ema = df['close'].ewm(span=param, adjust=False).mean()
        val = ema.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'WMA':
        if param < 1: return None
        wma = calculate_wma(df['close'], period=param)
        val = wma.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'RSI':
        if param < 1: return None
        rsi = calculate_rsi(df, period=param)
        val = rsi.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type in ('BB_UPPER', 'BB_MIDDLE', 'BB_LOWER'):
        if param < 1: return None
        std = bb_std if bb_std is not None else 2.0
        bb = calculate_bollinger(df['close'], period=param, std=std)
        val = bb[indicator_type].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VAL':
        return float(param)

    elif indicator_type == 'CLOSE':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VOLUME':
        val = df['volume'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VOLUME_PREV':
        prev_idx = target_idx - 1
        if abs(prev_idx) > len(df): return None
        val = df['volume'].iloc[prev_idx]
        multiplier = bb_std if bb_std is not None else 1.0
        val = val * multiplier
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VOLUME_MA':
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

    elif indicator_type == 'IC_TENKAN':
        p = max(1, param)
        high_val = df['high'].rolling(window=p).max()
        low_val = df['low'].rolling(window=p).min()
        val = ((high_val + low_val) / 2).iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_KIJUN':
        p = max(1, param)
        high_val = df['high'].rolling(window=p).max()
        low_val = df['low'].rolling(window=p).min()
        val = ((high_val + low_val) / 2).iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_SPAN_A':
        high_9 = df['high'].rolling(window=9).max()
        low_9 = df['low'].rolling(window=9).min()
        tenkan = (high_9 + low_9) / 2

        high_26 = df['high'].rolling(window=26).max()
        low_26 = df['low'].rolling(window=26).min()
        kijun = (high_26 + low_26) / 2

        span_a = (tenkan + kijun) / 2
        span_a_shifted = span_a.shift(param)
        val = span_a_shifted.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_SPAN_B':
        high_52 = df['high'].rolling(window=52).max()
        low_52 = df['low'].rolling(window=52).min()
        span_b = (high_52 + low_52) / 2
        span_b_shifted = span_b.shift(param)
        val = span_b_shifted.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_CHIKOU':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_CHIKOU_REF':
        val = df['close'].iloc[target_idx - param] if abs(target_idx - param) <= len(df) else None
        return None if pd.isna(val) else float(val)

    return None