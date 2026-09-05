from unittest.mock import patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import IntradayObservation


class IntradayObservationCommandTestCase(TestCase):
    def _frame(self):
        rows = []
        for index in range(62):
            close = 100.0 + index * 0.5
            rows.append({'open': close, 'high': close + 0.4, 'low': close - 0.5, 'close': close, 'volume': 100.0})
        rows[-2]['volume'] = 200.0
        return pd.DataFrame(rows)

    @patch('coinscreener.screener.management.commands.observe_intraday_danta.get_ohlcv_with_retry')
    @patch('coinscreener.screener.management.commands.observe_intraday_danta._get_tickers')
    def test_records_signal_once_per_15_minute_slot(self, mock_tickers, mock_ohlcv):
        mock_tickers.return_value = [{'ticker': 'KRW-TEST', 'name': 'TEST', 'current_price': 131.5}]
        mock_ohlcv.return_value = self._frame()

        call_command('observe_intraday_danta', '--force')
        call_command('observe_intraday_danta', '--force')

        observation = IntradayObservation.objects.get()
        self.assertEqual(IntradayObservation.objects.count(), 1)
        self.assertTrue(mock_ohlcv.call_args.kwargs['cache_only'])
        self.assertEqual(observation.ticker, 'KRW-TEST')
        self.assertEqual(observation.status, 'open')
        self.assertGreater(observation.target_2_price, observation.target_1_price)

