from unittest.mock import patch

import pandas as pd
from django.test import TestCase
from django.utils import timezone

from .management.commands.update_upbit_cache import Command
from .models import IntradayObservation


class IntradayObservationTrackingTestCase(TestCase):
    def _observation(self):
        return IntradayObservation.objects.create(
            detected_at=timezone.now(), ticker='KRW-TEST', name='TEST',
            entry_price=100, target_1_price=101.2, target_2_price=102.4,
            stop_loss=99.2, reason='test', strategy_version='test-v1',
        )

    @staticmethod
    def _frame(high, low, close):
        return pd.DataFrame([{
            'open': close, 'high': high, 'low': low, 'close': close, 'volume': 1,
        }])

    @patch('coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv')
    def test_target_one_stays_open_until_target_two(self, mock_ohlcv):
        observation = self._observation()
        mock_ohlcv.return_value = self._frame(101.3, 100, 101.1)
        Command()._monitor_intraday_observations()
        observation.refresh_from_db()
        self.assertEqual(observation.status, 'target_1')
        self.assertIsNone(observation.exit_price)

        mock_ohlcv.return_value = self._frame(102.5, 101, 102.3)
        Command()._monitor_intraday_observations()
        observation.refresh_from_db()
        self.assertEqual(observation.status, 'target_2')
        self.assertEqual(observation.exit_price, 102.4)
        self.assertAlmostEqual(observation.result_pct, 2.4)

    @patch('coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv')
    def test_stop_wins_when_same_candle_hits_stop_and_target(self, mock_ohlcv):
        observation = self._observation()
        mock_ohlcv.return_value = self._frame(102.5, 99.0, 101.0)
        Command()._monitor_intraday_observations()
        observation.refresh_from_db()
        self.assertEqual(observation.status, 'stopped')
        self.assertEqual(observation.exit_price, 99.2)
        self.assertAlmostEqual(observation.result_pct, -0.8)

