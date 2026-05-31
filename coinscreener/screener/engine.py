import pyupbit
import pandas as pd
import time


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


def check_strategy(ticker, conditions, current_price=None):
    """
    특정 코인이 주어진 전략(조건 리스트)을 모두 만족하는지 확인.
    조건이 비어있으면 매칭하지 않음.
    """
    # 조건이 없으면 모든 코인이 통과되는 버그 방지
    if not conditions:
        return False, [], None, 0

    try:
        data_cache = {}
        details = []
        last_price = None
        volume = 0

        for cond in conditions:
            if cond.timeframe not in data_cache:
                data_cache[cond.timeframe] = get_ohlcv_with_retry(ticker, interval=cond.timeframe)

            df = data_cache[cond.timeframe]

            if df is None:
                return False, [], None, 0

            if last_price is None:
                last_price = df['close'].iloc[-1]

            volume = df['value'].iloc[-1]

            # 지표 계산에 필요한 최소 데이터 길이 검증
            required_len = max(
                cond.left_param if cond.left_indicator not in ('VAL', 'CLOSE') else 0,
                cond.right_param if cond.right_indicator not in ('VAL', 'CLOSE') else 0,
            ) + cond.offset + 1

            if len(df) < required_len:
                return False, [], last_price, volume

            left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, cond.offset)
            right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, cond.offset)

            # 지표 계산 실패(NaN 포함) 시 조건 불충족으로 처리
            if left_val is None or right_val is None:
                return False, [], last_price, volume
            if pd.isna(left_val) or pd.isna(right_val):
                return False, [], last_price, volume

            if cond.left_indicator != 'VAL':
                details.append(f"{cond.left_indicator}({cond.left_param}): {left_val:.2f}")
            if cond.right_indicator != 'VAL':
                details.append(f"{cond.right_indicator}({cond.right_param}): {right_val:.2f}")

            if cond.operator == 'gt':
                if not (left_val > right_val):
                    return False, [], last_price, volume
            elif cond.operator == 'lt':
                if not (left_val < right_val):
                    return False, [], last_price, volume
            elif cond.operator == 'gte':
                if not (left_val >= right_val):
                    return False, [], last_price, volume
            elif cond.operator == 'lte':
                if not (left_val <= right_val):
                    return False, [], last_price, volume

        return True, details, last_price, volume

    except Exception as e:
        print(f"Error checking {ticker}: {e}")
        return False, [], None, 0


def get_indicator_value(df, indicator_type, param, offset):
    """
    DataFrame에서 특정 시점(offset)의 지표값을 반환.
    offset=0: 가장 최근 봉, offset=1: 1봉 전
    """
    target_idx = -1 - offset

    if abs(target_idx) > len(df):
        return None

    if indicator_type == 'MA':
        if param < 1:
            return None
        ma = df['close'].rolling(window=param).mean()
        val = ma.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'RSI':
        if param < 1:
            return None
        rsi = calculate_rsi(df, period=param)
        val = rsi.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VAL':
        return float(param)

    elif indicator_type == 'CLOSE':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    return None
