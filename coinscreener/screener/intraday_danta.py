"""Completed-candle intraday momentum signal rules."""
from __future__ import annotations

import math
import pandas as pd

INTRADAY_DANTA_STRATEGY_VERSION = 'danta-momentum-observe-v1.0'
MIN_ROWS = 61
EMA_FAST = 20
EMA_SLOW = 60
VOLUME_LOOKBACK = 20
BREAKOUT_LOOKBACK = 4
MIN_VOLUME_MULTIPLIER = 1.5
TARGET_1_PCT = 1.2
TARGET_2_PCT = 2.4
STOP_LOSS_PCT = 0.8


class SignalRejected(ValueError):
    """A candidate did not satisfy an explicit intraday safety condition."""


def _require_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {'close', 'high', 'volume'}
    if frame is None or len(frame) < MIN_ROWS or not required.issubset(frame.columns):
        raise SignalRejected('15분봉 이력 또는 필수 가격·거래량 데이터가 부족합니다.')
    return frame.copy()


def build_intraday_signal(ticker: str, frame: pd.DataFrame, live_price: float) -> dict:
    """Return a 1~3% intraday observation signal or raise SignalRejected."""
    frame = _require_frame(frame)
    try:
        live_price = float(live_price)
    except (TypeError, ValueError):
        raise SignalRejected('실시간 현재가가 유효하지 않습니다.')
    if not math.isfinite(live_price) or live_price <= 0:
        raise SignalRejected('실시간 현재가가 유효하지 않습니다.')

    completed = frame.iloc[:-1].copy()
    if len(completed) < MIN_ROWS - 1:
        raise SignalRejected('완료된 15분봉 이력이 부족합니다.')

    close = completed['close'].astype(float)
    high = completed['high'].astype(float)
    volume = completed['volume'].astype(float)
    ema_fast = close.ewm(span=EMA_FAST, adjust=False).mean().iloc[-1]
    ema_slow = close.ewm(span=EMA_SLOW, adjust=False).mean().iloc[-1]
    last_close = float(close.iloc[-1])
    if ema_fast <= ema_slow or last_close <= ema_fast:
        raise SignalRejected('완료봉 15분 추세가 상승 정렬이 아닙니다.')

    previous_volumes = volume.iloc[-(VOLUME_LOOKBACK + 1):-1]
    average_volume = float(previous_volumes.mean())
    confirmed_volume = float(volume.iloc[-1])
    if not math.isfinite(average_volume) or average_volume <= 0:
        raise SignalRejected('거래량 기준값이 유효하지 않습니다.')
    volume_ratio = confirmed_volume / average_volume
    if volume_ratio < MIN_VOLUME_MULTIPLIER:
        raise SignalRejected(f'완료봉 거래량이 {VOLUME_LOOKBACK}봉 평균의 {MIN_VOLUME_MULTIPLIER:.1f}배 미만입니다.')

    breakout_level = float(high.iloc[-BREAKOUT_LOOKBACK:].max())
    if live_price <= breakout_level:
        raise SignalRejected('실시간 가격이 최근 15분봉 고점을 돌파하지 않았습니다.')

    return {
        'ticker': ticker,
        'entry_price': live_price,
        'target_1_price': live_price * (1 + TARGET_1_PCT / 100),
        'target_2_price': live_price * (1 + TARGET_2_PCT / 100),
        'stop_loss': live_price * (1 - STOP_LOSS_PCT / 100),
        'target_1_pct': TARGET_1_PCT,
        'target_2_pct': TARGET_2_PCT,
        'stop_loss_pct': STOP_LOSS_PCT,
        'ema_fast': float(ema_fast),
        'ema_slow': float(ema_slow),
        'breakout_level': breakout_level,
        'volume_ratio': float(volume_ratio),
        'strategy_version': INTRADAY_DANTA_STRATEGY_VERSION,
        'reason': f'15분 EMA{EMA_FAST}>{EMA_SLOW} 상승 정렬 · 완료봉 거래량 {volume_ratio:.2f}배 · 실시간 {BREAKOUT_LOOKBACK}봉 고점 돌파',
    }
