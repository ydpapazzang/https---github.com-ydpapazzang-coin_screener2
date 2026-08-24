import io
import threading
from unittest.mock import patch

import pandas as pd
from django.test import TestCase

from .management.commands.update_upbit_cache import Command
from .models import Condition, Strategy


class MarketCacheCrawlerTestCase(TestCase):
    def setUp(self):
        strategy = Strategy.objects.create(name='Crawler test')
        Condition.objects.create(
            strategy=strategy,
            timeframe='day',
            left_indicator='CLOSE',
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )
        self.df = pd.DataFrame(
            {
                'open': [100.0],
                'high': [101.0],
                'low': [99.0],
                'close': [100.0],
                'volume': [10.0],
            },
            index=pd.date_range('2026-01-01', periods=1, freq='D'),
        )

    def test_fetch_retries_with_backoff_then_succeeds(self):
        command = Command()
        with patch.object(
            command,
            '_fetch_dataframe',
            side_effect=[RuntimeError('temporary'), self.df],
        ) as mock_fetch, patch(
            'coinscreener.screener.engine.prewarm_indicators'
        ) as mock_prewarm, patch(
            'coinscreener.screener.management.commands.update_upbit_cache.time.sleep'
        ) as mock_sleep:
            result = command._fetch_only(
                'KRW-BTC',
                'day',
                'upbit',
                specs=[],
            )

        self.assertIs(result['df'], self.df)
        self.assertEqual(result['attempts'], 2)
        self.assertIsNone(result['error'])
        self.assertEqual(mock_fetch.call_count, 2)
        mock_sleep.assert_called_once_with(0.5)
        mock_prewarm.assert_called_once_with(self.df, [])

    def test_fetch_reports_final_error_after_all_retries(self):
        command = Command()
        with patch.object(
            command,
            '_fetch_dataframe',
            side_effect=TypeError('bad payload'),
        ) as mock_fetch, patch(
            'coinscreener.screener.management.commands.update_upbit_cache.time.sleep'
        ) as mock_sleep:
            result = command._fetch_only(
                'BTC',
                'day',
                'bithumb',
                specs=[],
            )

        self.assertIsNone(result['df'])
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(result['error'], 'TypeError: bad payload')
        self.assertEqual(mock_fetch.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in mock_sleep.call_args_list],
            [0.5, 1.0],
        )

    def test_exchange_crawl_writes_database_results_on_caller_thread(self):
        command = Command(stdout=io.StringIO())
        caller_thread = threading.get_ident()
        write_threads = []
        result = {
            'ticker': 'KRW-BTC',
            'timeframe': 'day',
            'exchange': 'upbit',
            'df': self.df,
            'attempts': 1,
            'error': None,
        }

        def capture_store(_result):
            write_threads.append(threading.get_ident())

        with patch.object(
            command,
            '_fetch_only',
            return_value=result,
        ), patch.object(
            command,
            '_store_fetch_result',
            side_effect=capture_store,
        ):
            stats = command._crawl_exchange(
                'upbit',
                ['KRW-BTC'],
                ['day'],
                {'day': []},
            )

        self.assertEqual(stats['success'], 1)
        self.assertEqual(stats['failed'], 0)
        self.assertEqual(write_threads, [caller_thread])

    def test_exchange_crawl_counts_and_logs_failed_items(self):
        output = io.StringIO()
        command = Command(stdout=output)
        result = {
            'ticker': 'KRW-BTC',
            'timeframe': 'day',
            'exchange': 'upbit',
            'df': None,
            'attempts': 3,
            'error': 'RuntimeError: empty OHLCV response',
        }

        with patch.object(command, '_fetch_only', return_value=result):
            stats = command._crawl_exchange(
                'upbit',
                ['KRW-BTC'],
                ['day'],
                {'day': []},
            )

        self.assertEqual(stats['requested'], 1)
        self.assertEqual(stats['success'], 0)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['retried'], 2)
        self.assertIn('[CRAWLER_ITEM_ERROR]', output.getvalue())
        self.assertIn('[CRAWLER_EXCHANGE_SUMMARY]', output.getvalue())

    @patch(
        'coinscreener.screener.engine.indicator_specs_by_timeframe',
        return_value={},
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pybithumb.get_tickers',
        return_value=['BTC'],
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_tickers',
        side_effect=RuntimeError('upbit unavailable'),
    )
    def test_ticker_list_failure_does_not_block_other_exchange(
        self,
        _mock_upbit,
        _mock_bithumb,
        _mock_specs,
    ):
        output = io.StringIO()
        command = Command(stdout=output)
        stats = {
            'requested': 1,
            'success': 1,
            'failed': 0,
            'retried': 0,
        }

        with patch.object(
            command,
            '_crawl_exchange',
            return_value=stats,
        ) as mock_crawl:
            command._run_crawler()

        mock_crawl.assert_called_once()
        self.assertEqual(mock_crawl.call_args.args[0], 'bithumb')
        self.assertIn(
            '[CRAWLER_EXCHANGE_ERROR] exchange=upbit stage=ticker_list',
            output.getvalue(),
        )
        self.assertIn(
            '[CRAWLER_CYCLE_SUMMARY] requested=1 success=1 failed=0',
            output.getvalue(),
        )

    @patch(
        'coinscreener.screener.engine.indicator_specs_by_timeframe',
        return_value={},
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pybithumb.get_tickers',
        return_value=['BTC'],
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_tickers',
        return_value=['KRW-BTC'],
    )
    def test_exchange_crawl_failure_does_not_block_next_exchange(
        self,
        _mock_upbit,
        _mock_bithumb,
        _mock_specs,
    ):
        output = io.StringIO()
        command = Command(stdout=output)
        bithumb_stats = {
            'requested': 1,
            'success': 1,
            'failed': 0,
            'retried': 0,
        }

        with patch.object(
            command,
            '_crawl_exchange',
            side_effect=[RuntimeError('upbit failed'), bithumb_stats],
        ) as mock_crawl:
            command._run_crawler()

        self.assertEqual(mock_crawl.call_count, 2)
        self.assertEqual(mock_crawl.call_args_list[1].args[0], 'bithumb')
        self.assertIn(
            '[CRAWLER_EXCHANGE_ERROR] exchange=upbit stage=crawl',
            output.getvalue(),
        )
        self.assertIn(
            '[CRAWLER_CYCLE_SUMMARY] requested=1 success=1 failed=0',
            output.getvalue(),
        )
