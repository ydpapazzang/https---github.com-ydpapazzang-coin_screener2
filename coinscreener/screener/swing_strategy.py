from datetime import timedelta
import math

import numpy as np
import pandas as pd

from .daily_picks import RecommendationRejected, is_stablecoin_ticker


MIN_COMPLETED_CANDLES = 180
MAX_ENTRY_GAP_PCT = 2.0
MAX_ALREADY_CROSSED_PCT = 1.0
MAX_DAILY_JUMP_PCT = 10.0
MAX_ATR_PCT = 12.0
MIN_STOP_DISTANCE_PCT = 2.0
MAX_STOP_DISTANCE_PCT = 8.0
ENTRY_VALID_DAYS = 2
MAX_HOLD_DAYS = 20
MAX_OPEN_POSITIONS = 3
RISK_PER_TRADE_PCT = 0.5

_REQUIRED_COLUMNS = ('open', 'high', 'low', 'close', 'value')


def _round_price(price):
    return round(price, 2) if price < 100 else int(price)


def validate_swing_candles(df, today_date):
    """Validate 180 completed daily candles plus today's in-progress candle."""
    required_count = MIN_COMPLETED_CANDLES + 1
    if df is None or len(df) < required_count:
        raise RecommendationRejected(
            f"일봉 데이터가 {required_count}개 미만입니다."
        )

    missing = [column for column in _REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise RecommendationRejected(
            f"필수 일봉 컬럼이 없습니다: {', '.join(missing)}"
        )

    candles = df.copy().tail(required_count)
    numeric = candles.loc[:, list(_REQUIRED_COLUMNS)].apply(
        pd.to_numeric, errors='coerce'
    )
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise RecommendationRejected("일봉 데이터에 NaN 또는 무한대가 있습니다.")
    if (numeric <= 0).to_numpy().any():
        raise RecommendationRejected("일봉 데이터에 0 이하 값이 있습니다.")

    invalid_ohlc = (
        (numeric['high'] < numeric[['open', 'close']].max(axis=1))
        | (numeric['low'] > numeric[['open', 'close']].min(axis=1))
        | (numeric['high'] < numeric['low'])
    )
    if invalid_ohlc.any():
        raise RecommendationRejected("일봉 OHLC 관계가 올바르지 않습니다.")
    if not candles.index.is_monotonic_increasing or candles.index.has_duplicates:
        raise RecommendationRejected("일봉 시간이 정렬되지 않았거나 중복되었습니다.")

    last_date = pd.Timestamp(candles.index[-1]).date()
    previous_date = pd.Timestamp(candles.index[-2]).date()
    if last_date != today_date or previous_date != today_date - timedelta(days=1):
        raise RecommendationRejected(
            f"최신 일봉 날짜가 맞지 않습니다: {previous_date} / {last_date}"
        )

    candles.loc[:, list(_REQUIRED_COLUMNS)] = numeric
    return candles


def _trend_metrics(candles):
    completed = candles.iloc[:-1]
    close = completed['close'].astype(float)
    high = completed['high'].astype(float)
    low = completed['low'].astype(float)

    ema20_series = close.ewm(span=20, adjust=False).mean()
    ema60_series = close.ewm(span=60, adjust=False).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = float(true_range.rolling(14).mean().iloc[-1])

    return {
        'completed': completed,
        'close': float(close.iloc[-1]),
        'ema20': float(ema20_series.iloc[-1]),
        'ema20_5d_ago': float(ema20_series.iloc[-6]),
        'ema60': float(ema60_series.iloc[-1]),
        'atr14': atr14,
        'momentum20_pct': float((close.iloc[-1] / close.iloc[-21] - 1) * 100),
        'change_3d_pct': float((close.iloc[-1] / close.iloc[-4] - 1) * 100),
        'breakout_20': float(high.tail(20).max()),
        'median_value_20': float(
            completed['value'].astype(float).tail(20).median()
        ),
    }


def validate_btc_regime(df, today_date):
    candles = validate_swing_candles(df, today_date)
    metrics = _trend_metrics(candles)

    if not (
        metrics['close'] > metrics['ema60']
        and metrics['ema20'] > metrics['ema60']
        and metrics['ema20'] > metrics['ema20_5d_ago']
    ):
        raise RecommendationRejected(
            "BTC 일봉 추세가 상승 조건(종가·EMA20 > EMA60)을 충족하지 않습니다."
        )
    if metrics['change_3d_pct'] <= -12:
        raise RecommendationRejected(
            f"BTC 최근 3일 급락({metrics['change_3d_pct']:.1f}%) 구간입니다."
        )
    return metrics


def build_swing_recommendation(
    ticker,
    coin_name,
    df,
    current_price,
    today_date,
):
    if is_stablecoin_ticker(ticker):
        raise RecommendationRejected("스테이블코인은 스윙 추천에서 제외합니다.")

    candles = validate_swing_candles(df, today_date)
    metrics = _trend_metrics(candles)

    try:
        current_price = float(current_price)
    except (TypeError, ValueError):
        raise RecommendationRejected("현재가가 숫자가 아닙니다.")
    if not math.isfinite(current_price) or current_price <= 0:
        raise RecommendationRejected("현재가가 유효하지 않습니다.")

    if not (
        metrics['close'] > metrics['ema20'] > metrics['ema60']
        and metrics['ema20'] > metrics['ema20_5d_ago']
    ):
        raise RecommendationRejected("종가·EMA20·EMA60 상승 정렬이 아닙니다.")
    if metrics['momentum20_pct'] <= 0:
        raise RecommendationRejected("최근 20일 모멘텀이 0% 이하입니다.")

    atr_pct = metrics['atr14'] / current_price * 100
    if not math.isfinite(atr_pct) or atr_pct > MAX_ATR_PCT:
        raise RecommendationRejected(
            f"ATR 변동성({atr_pct:.1f}%)이 허용 범위를 초과했습니다."
        )

    entry_price = metrics['breakout_20']
    entry_gap_pct = (entry_price - current_price) / current_price * 100
    if entry_gap_pct > MAX_ENTRY_GAP_PCT:
        raise RecommendationRejected(
            f"진입가가 현재가보다 {entry_gap_pct:.1f}% 높습니다."
        )
    if entry_gap_pct < -MAX_ALREADY_CROSSED_PCT:
        raise RecommendationRejected(
            f"현재가가 진입가를 {-entry_gap_pct:.1f}% 초과해 추격 진입을 차단합니다."
        )

    today_open = float(candles.iloc[-1]['open'])
    daily_jump_pct = (current_price - today_open) / today_open * 100
    if daily_jump_pct >= MAX_DAILY_JUMP_PCT:
        raise RecommendationRejected(
            f"당일 급등률({daily_jump_pct:.1f}%)이 10% 이상입니다."
        )

    raw_stop = max(metrics['ema20'], entry_price - 2 * metrics['atr14'])
    stop_loss = min(raw_stop, entry_price * (1 - MIN_STOP_DISTANCE_PCT / 100))
    stop_distance_pct = (entry_price - stop_loss) / entry_price * 100
    if stop_distance_pct > MAX_STOP_DISTANCE_PCT:
        raise RecommendationRejected(
            f"필요 손절 폭({stop_distance_pct:.1f}%)이 8%를 초과합니다."
        )

    risk_amount = entry_price - stop_loss
    target_price = entry_price + 2 * risk_amount
    trend_strength_pct = (metrics['close'] / metrics['ema60'] - 1) * 100

    return {
        'ticker': ticker,
        'name': coin_name,
        'entry_price': _round_price(entry_price),
        'target_price': _round_price(target_price),
        'stop_loss': _round_price(stop_loss),
        'entry_expires_on': today_date + timedelta(days=ENTRY_VALID_DAYS),
        'momentum20_pct': metrics['momentum20_pct'],
        'trend_strength_pct': trend_strength_pct,
        'atr_pct': atr_pct,
        'median_value_20': metrics['median_value_20'],
        'entry_gap_pct': entry_gap_pct,
        'stop_distance_pct': stop_distance_pct,
    }


def rank_swing_recommendations(recommendations, limit=MAX_OPEN_POSITIONS):
    return sorted(
        recommendations,
        key=lambda item: (
            item['momentum20_pct'],
            item['trend_strength_pct'],
            item.get('median_value_20', 0),
        ),
        reverse=True,
    )[:limit]
