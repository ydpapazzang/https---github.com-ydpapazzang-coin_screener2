import pyupbit
import pandas as pd
import time
import numpy as np


def get_ohlcv_with_retry(ticker, interval, count=200, retries=3, delay=0.3):
    """API 호출 제한을 고려하여 재시도 로직이 포함된 OHLCV 조회"""
    for i in range(retries):
        time.sleep(0.1)
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        if df is not None:
            return df
        time.sleep(delay * (i + 1))
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
                total_offset = cond.offset + base_offset

                # 지표 계산에 필요한 최소 데이터 길이 검증
                required_len = max(
                    cond.left_param if cond.left_indicator not in ('VAL', 'CLOSE') else 0,
                    cond.right_param if cond.right_indicator not in ('VAL', 'CLOSE') else 0,
                ) + total_offset + 1

                if len(df) < required_len: return False

                left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, total_offset)
                right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, total_offset)

                if left_val is None or right_val is None or pd.isna(left_val) or pd.isna(right_val):
                    return False

                op_map = {'gt': left_val > right_val, 'lt': left_val < right_val, 'gte': left_val >= right_val, 'lte': left_val <= right_val}
                if not op_map.get(cond.operator):
                    return False
            return True

        # 1. 현재 봉(base_offset=0) 조건 확인
        is_match_current = _check_for_offset(0)

        # 현재가와 거래대금은 데이터가 있으면 항상 가져옴
        if conditions and conditions[0].timeframe in data_cache:
            df = data_cache[conditions[0].timeframe]
            if not df.empty:
                last_price = df['close'].iloc[-1]
                volume = df['value'].iloc[-1]

        if not is_match_current:
            return False, [], last_price, volume, None

        # 2. 이전 봉(base_offset=1) 조건 확인
        is_match_previous = _check_for_offset(1)
        status = 'maintained' if is_match_previous else 'new'

        # 3. 현재 봉 기준 details 수집
        for cond in conditions:
            df = data_cache[cond.timeframe]
            left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, cond.offset)
            right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, cond.offset)

            if cond.left_indicator != 'VAL':
                if left_val is not None and not pd.isna(left_val):
                    details.append(f"{cond.left_indicator}({cond.left_param}): {left_val:.2f}")
            if cond.right_indicator != 'VAL':
                if right_val is not None and not pd.isna(right_val):
                    details.append(f"{cond.right_indicator}({cond.right_param}): {right_val:.2f}")
        
        return True, details, last_price, volume, status

    except Exception as e:
        print(f"Error checking {ticker}: {e}")
        return False, [], None, 0, None


def get_indicator_value(df, indicator_type, param, offset):
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
        # TODO: Condition 모델의 bb_std 필드를 활용하도록 개선 가능
        std = 2.0
        bb = calculate_bollinger(df['close'], period=param, std=std)
        val = bb[indicator_type].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VAL':
        return float(param)

    elif indicator_type == 'CLOSE':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    return None
