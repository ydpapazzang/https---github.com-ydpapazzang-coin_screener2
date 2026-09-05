import pandas as pd
from django.test import SimpleTestCase

from .intraday_danta import SignalRejected, build_intraday_signal


class IntradayDantaSignalTestCase(SimpleTestCase):
    def _frame(self, volume=200.0):
        rows = []
        for index in range(62):
            close = 100.0 + index * 0.5
            rows.append({'open': close - 0.2, 'high': close + 0.4, 'low': close - 0.5, 'close': close, 'volume': 100.0})
        rows[-2]['volume'] = volume
        return pd.DataFrame(rows)

    def test_signal_uses_completed_candles_and_live_breakout_price(self):
        signal = build_intraday_signal('KRW-TEST', self._frame(), live_price=131.5)
        self.assertEqual(signal['ticker'], 'KRW-TEST')
        self.assertAlmostEqual(signal['target_1_price'], 133.078)
        self.assertAlmostEqual(signal['target_2_price'], 134.656)
        self.assertAlmostEqual(signal['stop_loss'], 130.448)
        self.assertGreaterEqual(signal['volume_ratio'], 1.5)

    def test_rejects_without_confirmed_volume_surge(self):
        with self.assertRaisesRegex(SignalRejected, '거래량'):
            build_intraday_signal('KRW-TEST', self._frame(volume=120.0), 131.5)

    def test_rejects_when_live_price_has_not_broken_out(self):
        with self.assertRaisesRegex(SignalRejected, '고점을 돌파'):
            build_intraday_signal('KRW-TEST', self._frame(), 130.0)
