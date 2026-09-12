from datetime import datetime, timedelta

import pandas as pd
from django.test import SimpleTestCase

from .danta_dual_timeframe import (
    SignalRejected, V2_AGGRESSIVE_PROFILE, build_pullback_signal,
)


class DualTimeframeDantaRuleTestCase(SimpleTestCase):
    def _hourly_frame(self):
        rows = 211  # last row is deliberately unfinished
        index = [datetime(2026, 1, 1) + timedelta(hours=i) for i in range(rows)]
        frame = pd.DataFrame({
            'open': [100.0] * rows, 'high': [100.0] * rows,
            'low': [100.0] * rows, 'close': [100.0] * rows,
            'volume': [100.0] * rows,
        }, index=index)
        # The latest completed candle is the long-bias confirmation.
        # 최근 26개 봉의 고가 상승으로 양운·전환선/기준선 정배열을 만든다.
        frame.iloc[-27:-1, frame.columns.get_loc('high')] = 101.0
        frame.iloc[-10:-1, frame.columns.get_loc('high')] = 102.0
        frame.iloc[-52, frame.columns.get_loc('low')] = 98.0
        frame.iloc[-2, frame.columns.get_loc('close')] = 102.0
        return frame

    def _five_minute_frame(self):
        rows = 71  # last row is deliberately unfinished
        index = [datetime(2026, 1, 1) + timedelta(minutes=5 * i) for i in range(rows)]
        frame = pd.DataFrame({
            'open': [100.0] * rows, 'high': [100.0] * rows,
            'low': [100.0] * rows, 'close': [100.0] * rows,
            'volume': [100.0] * rows,
        }, index=index)
        # Impulse on the prior completed bar.
        frame.iloc[-3, frame.columns.get_loc('close')] = 105.0
        frame.iloc[-3, frame.columns.get_loc('high')] = 106.0
        frame.iloc[-3, frame.columns.get_loc('volume')] = 300.0
        # First pullback: support touch, reduced volume, and bullish close.
        # A preceding low keeps the Kijun near the pullback price, making this
        # a valid touch-and-hold setup rather than a candle entirely below it.
        frame.iloc[-20, frame.columns.get_loc('low')] = 94.0
        frame.iloc[-2, frame.columns.get_loc('open')] = 99.8
        frame.iloc[-2, frame.columns.get_loc('high')] = 100.3
        frame.iloc[-2, frame.columns.get_loc('low')] = 99.7
        frame.iloc[-2, frame.columns.get_loc('close')] = 100.1
        frame.iloc[-2, frame.columns.get_loc('volume')] = 50.0
        return frame

    def test_completed_candle_pullback_creates_risk_bounded_signal(self):
        signal = build_pullback_signal(self._hourly_frame(), self._five_minute_frame())

        self.assertTrue(signal['support_confirmed'])
        self.assertLess(signal['stop_loss'], signal['entry_price'])
        self.assertGreaterEqual(
            (signal['entry_price'] - signal['stop_loss']) / signal['entry_price'] * 100,
            0,
        )
        self.assertLessEqual(
            (signal['entry_price'] - signal['stop_loss']) / signal['entry_price'] * 100,
            1.0,
        )
        self.assertGreaterEqual(signal['risk_reward'], 1.8)

    def test_hourly_overextension_is_rejected(self):
        hourly = self._hourly_frame()
        hourly.iloc[-2, hourly.columns.get_loc('close')] = 110.0
        hourly.iloc[-2, hourly.columns.get_loc('high')] = 110.0

        with self.assertRaisesRegex(SignalRejected, '이격 과열'):
            build_pullback_signal(hourly, self._five_minute_frame())

    def test_pullback_closing_below_support_is_rejected(self):
        five_minute = self._five_minute_frame()
        five_minute.iloc[-2, five_minute.columns.get_loc('open')] = 99.4
        five_minute.iloc[-2, five_minute.columns.get_loc('high')] = 99.8
        five_minute.iloc[-2, five_minute.columns.get_loc('low')] = 99.2
        five_minute.iloc[-2, five_minute.columns.get_loc('close')] = 99.5

        with self.assertRaisesRegex(SignalRejected, '지지 아래에서 마감'):
            build_pullback_signal(self._hourly_frame(), five_minute)

    def test_aggressive_profile_is_recorded_in_signal(self):
        signal = build_pullback_signal(
            self._hourly_frame(), self._five_minute_frame(),
            profile=V2_AGGRESSIVE_PROFILE,
        )
        self.assertEqual(signal['profile'].key, 'v2')
        self.assertGreaterEqual(signal['risk_reward'], V2_AGGRESSIVE_PROFILE.min_risk_reward)

