from unittest.mock import patch

import pandas as pd
from django.core.cache import cache
from django.test import TestCase

from .engine import get_ohlcv_with_retry


class UpbitOhlcvHistoryTestCase(TestCase):
    def setUp(self):
        cache.clear()

    @staticmethod
    def _frame(start, periods):
        index = pd.date_range(start=start, periods=periods, freq='h')
        return pd.DataFrame(
            {
                'open': range(periods),
                'high': range(1, periods + 1),
                'low': range(periods),
                'close': range(1, periods + 1),
                'volume': [100] * periods,
            },
            index=index,
        )

    @patch('coinscreener.screener.engine._throttle')
    @patch('coinscreener.screener.engine.pyupbit.get_ohlcv')
    def test_upbit_request_over_200_paginates_history(self, mock_get_ohlcv, _mock_throttle):
        older = self._frame('2026-01-01', 60)
        recent = self._frame('2026-01-03 12:00:00', 200)
        mock_get_ohlcv.side_effect = [recent, older]

        result = get_ohlcv_with_retry(
            'KRW-BTC', 'minute60', count=260, retries=1, persist_db=False,
        )

        self.assertEqual(len(result), 260)
        self.assertEqual(mock_get_ohlcv.call_count, 2)
        self.assertEqual(mock_get_ohlcv.call_args_list[0].kwargs['count'], 200)
        self.assertEqual(mock_get_ohlcv.call_args_list[1].kwargs['count'], 60)
        self.assertEqual(mock_get_ohlcv.call_args_list[1].kwargs['to'], recent.index.min().to_pydatetime())

    @patch('coinscreener.screener.engine._throttle')
    @patch('coinscreener.screener.engine.pyupbit.get_ohlcv')
    def test_short_cache_does_not_block_long_history_request(self, mock_get_ohlcv, _mock_throttle):
        cached = self._frame('2026-01-03 12:00:00', 200)
        older = self._frame('2026-01-01', 60)
        cache.set('ohlcv_KRW-ETH_minute60_260', cached, 180)
        mock_get_ohlcv.side_effect = [cached, older]

        result = get_ohlcv_with_retry(
            'KRW-ETH', 'minute60', count=260, retries=1, persist_db=False,
        )

        self.assertEqual(len(result), 260)
        self.assertEqual(mock_get_ohlcv.call_count, 2)
