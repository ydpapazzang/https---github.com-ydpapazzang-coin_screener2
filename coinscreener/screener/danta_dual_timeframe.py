"""Closed-candle 1H/5M momentum-pullback danta rules.

This module intentionally contains no database or network code so the rules can
be tested with fixed OHLCV frames.  The scheduler owns persistence and alerts.
"""
import math
from dataclasses import dataclass

import pandas as pd


DUAL_DANTA_STRATEGY_VERSION = 'danta-1h5m-pullback-v1.0'
AGGRESSIVE_DANTA_STRATEGY_VERSION = 'danta-1h5m-pullback-v2-aggressive'
HOURLY_MA_PERIOD = 200
ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SPAN_B = 52
SETUP_WINDOW_CANDLES = 6
VOLUME_EXPANSION_MULTIPLIER = 2.0
SUPPORT_TOUCH_TOLERANCE = 0.002
SUPPORT_CLOSE_TOLERANCE = 0.002
MAX_KIJUN_DISTANCE_PCT = 2.5
MAX_STOP_LOSS_PCT = 1.0
MIN_RISK_REWARD = 1.8


@dataclass(frozen=True)
class DantaProfile:
    key: str
    label: str
    strategy_version: str
    universe_limit: int
    setup_window_candles: int
    volume_expansion_multiplier: float
    max_kijun_distance_pct: float
    min_risk_reward: float
    first_take_profit_fraction: float


V1_PROFILE = DantaProfile(
    key='v1', label='V1 안정형', strategy_version=DUAL_DANTA_STRATEGY_VERSION,
    universe_limit=30, setup_window_candles=6, volume_expansion_multiplier=2.0,
    max_kijun_distance_pct=2.5, min_risk_reward=1.8,
    first_take_profit_fraction=0.5,
)
V2_AGGRESSIVE_PROFILE = DantaProfile(
    key='v2', label='V2 공격형', strategy_version=AGGRESSIVE_DANTA_STRATEGY_VERSION,
    universe_limit=60, setup_window_candles=12, volume_expansion_multiplier=1.5,
    max_kijun_distance_pct=4.0, min_risk_reward=1.5,
    first_take_profit_fraction=0.7,
)
DANTA_PROFILES = {profile.key: profile for profile in (V1_PROFILE, V2_AGGRESSIVE_PROFILE)}


class SignalRejected(ValueError):
    """The candidate did not satisfy the rules; this is not an operational error."""


def _completed(frame, minimum, label):
    if frame is None or len(frame) < minimum + 1:
        raise SignalRejected(f'{label} 마감봉 데이터가 부족합니다.')
    completed = frame.iloc[:-1].copy()
    required = {'open', 'high', 'low', 'close', 'volume'}
    if not required.issubset(completed.columns):
        raise SignalRejected(f'{label} 필수 OHLCV 컬럼이 없습니다.')
    if completed[list(required)].isnull().values.any():
        raise SignalRejected(f'{label} 데이터에 빈 값이 있습니다.')
    return completed.astype(float)


def _ichimoku(frame):
    high, low = frame['high'], frame['low']
    tenkan = (high.rolling(ICHIMOKU_TENKAN).max() + low.rolling(ICHIMOKU_TENKAN).min()) / 2
    kijun = (high.rolling(ICHIMOKU_KIJUN).max() + low.rolling(ICHIMOKU_KIJUN).min()) / 2
    span_a = (tenkan + kijun) / 2
    span_b = (high.rolling(ICHIMOKU_SPAN_B).max() + low.rolling(ICHIMOKU_SPAN_B).min()) / 2
    return tenkan, kijun, span_a, span_b


def hourly_trend(frame, profile=V1_PROFILE):
    """Return the 1H long-bias snapshot or reject it, using completed candles only."""
    data = _completed(frame, HOURLY_MA_PERIOD, '1시간봉')
    close = data['close']
    ma200 = close.rolling(HOURLY_MA_PERIOD).mean()
    tenkan, kijun, span_a, span_b = _ichimoku(data)
    last = data.iloc[-1]
    values = {
        'close': float(last['close']),
        'ma200': float(ma200.iloc[-1]),
        'tenkan': float(tenkan.iloc[-1]),
        'kijun': float(kijun.iloc[-1]),
        'span_a': float(span_a.iloc[-1]),
        'span_b': float(span_b.iloc[-1]),
        'chikou_reference_close': float(close.iloc[-1 - ICHIMOKU_KIJUN]),
    }
    if not all(math.isfinite(value) and value > 0 for value in values.values()):
        raise SignalRejected('1시간봉 지표 계산값이 유효하지 않습니다.')
    cloud_top = max(values['span_a'], values['span_b'])
    distance_pct = (values['close'] - values['kijun']) / values['kijun'] * 100
    checks = (
        (values['close'] > values['ma200'], '종가가 MA200 아래'),
        (values['close'] > cloud_top, '종가가 구름대 아래'),
        (values['span_a'] > values['span_b'], '음운 상태'),
        (values['tenkan'] >= values['kijun'], '전환선이 기준선 아래'),
        (values['close'] > values['chikou_reference_close'], '후행스팬이 과거 종가 아래'),
        (distance_pct <= profile.max_kijun_distance_pct, '기준선 이격 과열'),
    )
    for passed, reason in checks:
        if not passed:
            raise SignalRejected(f'1시간 추세 필터 제외: {reason}')
    values.update({'cloud_top': cloud_top, 'kijun_distance_pct': distance_pct})
    return values


def build_pullback_signal(hourly_frame, five_minute_frame, profile=V1_PROFILE):
    """Find the first qualifying 5M pullback after an impulse in the last 30m."""
    trend = hourly_trend(hourly_frame, profile)
    data = _completed(five_minute_frame, 60, '5분봉')
    close, high, low, volume = data['close'], data['high'], data['low'], data['volume']
    mid = close.rolling(20).mean()
    std = close.rolling(20).std(ddof=0)
    upper = mid + 2.0 * std
    volume_ma = volume.shift(1).rolling(20).mean()
    _tenkan, kijun, _span_a, _span_b = _ichimoku(data)

    # Setup must precede the current pullback candle and be no older than six 5M bars.
    setup_indexes = []
    for position in range(len(data) - profile.setup_window_candles - 1, len(data) - 1):
        if position < 20:
            continue
        if volume.iloc[position] >= volume_ma.iloc[position] * profile.volume_expansion_multiplier and high.iloc[position] >= upper.iloc[position]:
            setup_indexes.append(position)
    if not setup_indexes:
        raise SignalRejected(
            f'5분봉 최근 {profile.setup_window_candles * 5}분 내 '
            '거래량·볼린저 분출 셋업이 없습니다.'
        )

    current = len(data) - 1
    support = max(float(mid.iloc[current]), float(kijun.iloc[current]))
    candle_open = float(data['open'].iloc[current])
    candle_close = float(close.iloc[current])
    candle_high = float(high.iloc[current])
    candle_low = float(low.iloc[current])
    candle_range = candle_high - candle_low
    lower_wick = min(candle_open, candle_close) - candle_low
    rebound = candle_close > candle_open or (
        candle_range > 0 and lower_wick > candle_range * 0.35
    )
    # A wick at support is only a pullback when the completed candle holds the
    # level. Checking the low alone accepted candles whose whole body closed
    # below the moving-average/Kijun support.
    support_touched = candle_low <= support * (1 + SUPPORT_TOUCH_TOLERANCE)
    support_held = candle_close >= support * (1 - SUPPORT_CLOSE_TOLERANCE)
    volume_contracting = volume.iloc[current] < volume_ma.iloc[current]
    if not volume_contracting:
        raise SignalRejected('5분봉 눌림목 거래량이 아직 감소하지 않았습니다.')
    if not support_touched:
        raise SignalRejected('5분봉 눌림목이 중심선·기준선 지지에 닿지 않았습니다.')
    if not support_held:
        raise SignalRejected('5분봉이 중심선·기준선 지지 아래에서 마감했습니다.')
    if not rebound:
        raise SignalRejected('5분봉 반등 마감(양봉/아래꼬리)이 확인되지 않았습니다.')

    entry = candle_close
    raw_stop = min(candle_low, support * 0.995)
    stop = max(raw_stop, entry * (1 - MAX_STOP_LOSS_PCT / 100))
    if not 0 < stop < entry:
        raise SignalRejected('손절가가 진입가보다 낮게 계산되지 않았습니다.')
    target_1 = float(upper.iloc[current])
    risk = entry - stop
    if target_1 <= entry or (target_1 - entry) / risk < profile.min_risk_reward:
        raise SignalRejected(f'볼린저 상단 기준 손익비가 1:{profile.min_risk_reward:.1f} 미만입니다.')

    return {
        'entry_price': entry,
        'target_1_price': target_1,
        'stop_loss': stop,
        'support_level': support,
        'support_confirmed': True,
        'setup_candles_ago': current - setup_indexes[-1],
        'kijun_distance_pct': trend['kijun_distance_pct'],
        'hourly_kijun': trend['kijun'],
        'hourly_close': trend['close'],
        'risk_reward': (target_1 - entry) / risk,
        'trend': trend,
        'profile': profile,
    }

