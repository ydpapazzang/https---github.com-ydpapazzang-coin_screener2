import pyupbit
import pandas as pd
import numpy as np
import time


def get_ohlcv_with_retry(ticker, interval, count=200, retries=3, delay=0.3):
    for i in range(retries):
        time.sleep(0.1)
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        if df is not None:
            return df
        time.sleep(delay * (i + 1))
    return None


def calculate_rsi(ohlc: pd.DataFrame, period: int = 14):
    delta = ohlc["close"].diff()
    ups   = delta.clip(lower=0)
    downs = (-delta).clip(lower=0)
    au = ups.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    ad = downs.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    RS  = au / ad.replace(0, float('nan'))
    rsi = 100 - (100 / (1 + RS))
    return pd.Series(rsi, name="RSI")


def calculate_wma(series: pd.Series, period: int) -> pd.Series:
    """가중이동평균 (선형 가중치)"""
    weights = np.arange(1, period + 1, dtype=float)
    def _wma(x):
        if len(x) < period:
            return np.nan
        return float(np.dot(x[-period:], weights) / weights.sum())
    return series.rolling(window=period).apply(_wma, raw=True)


def calculate_bollinger(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0):
    mid   = df['close'].rolling(window=period).mean()
    std   = df['close'].rolling(window=period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def check_strategy(ticker, conditions, current_price=None):
    if not conditions:
        return False, [], None, 0

    try:
        data_cache = {}
        details    = []
        last_price = None
        volume     = 0

        for cond in conditions:
            if cond.timeframe not in data_cache:
                data_cache[cond.timeframe] = get_ohlcv_with_retry(ticker, interval=cond.timeframe)

            df = data_cache[cond.timeframe]
            if df is None:
                return False, [], None, 0

            if last_price is None:
                last_price = df['close'].iloc[-1]
            volume = df['value'].iloc[-1]

            required_len = max(
                cond.left_param  if cond.left_indicator  not in ('VAL', 'CLOSE') else 0,
                cond.right_param if cond.right_indicator not in ('VAL', 'CLOSE') else 0,
            ) + cond.offset + 1

            if len(df) < required_len:
                return False, [], last_price, volume

            left_val  = get_indicator_value(df, cond.left_indicator,  cond.left_param,  cond.offset)
            right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, cond.offset)

            if left_val is None or right_val is None:
                return False, [], last_price, volume
            if pd.isna(left_val) or pd.isna(right_val):
                return False, [], last_price, volume

            if cond.left_indicator  != 'VAL':
                details.append(f"{cond.left_indicator}({cond.left_param}): {left_val:.2f}")
            if cond.right_indicator != 'VAL':
                details.append(f"{cond.right_indicator}({cond.right_param}): {right_val:.2f}")

            ops = {
                'gt':  lambda l, r: l >  r,
                'lt':  lambda l, r: l <  r,
                'gte': lambda l, r: l >= r,
                'lte': lambda l, r: l <= r,
            }
            if not ops.get(cond.operator, lambda l, r: False)(left_val, right_val):
                return False, [], last_price, volume

        return True, details, last_price, volume

    except Exception as e:
        print(f"Error checking {ticker}: {e}")
        return False, [], None, 0


def get_indicator_value(df, indicator_type, param, offset):
    target_idx = -1 - offset
    if abs(target_idx) > len(df):
        return None

    if indicator_type == 'MA':          # SMA
        if param < 1: return None
        val = df['close'].rolling(window=param).mean().iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'EMA':
        if param < 1: return None
        val = df['close'].ewm(span=param, adjust=False).mean().iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'WMA':
        if param < 1: return None
        val = calculate_wma(df['close'], param).iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'RSI':
        if param < 1: return None
        val = calculate_rsi(df, period=param).iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type in ('BB_UPPER', 'BB_MIDDLE', 'BB_LOWER'):
        if param < 1: return None
        upper, mid, lower = calculate_bollinger(df, period=param)
        series = {'BB_UPPER': upper, 'BB_MIDDLE': mid, 'BB_LOWER': lower}[indicator_type]
        val = series.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VAL':
        return float(param)

    elif indicator_type == 'CLOSE':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    return None
