from datetime import timedelta
import threading
from unittest.mock import patch

import pandas as pd
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .management.commands.update_upbit_cache import Command
from .models import DailyRecommendation


class StatsListViewTestCase(TestCase):
    def _recommendation(self, **overrides):
        values = {
            'date': timezone.localdate(),
            'coin_ticker': 'KRW-BTC',
            'coin_name': 'BTC',
            'entry_price': 100.0,
            'target_price': 102.0,
            'stop_loss': 98.5,
            'k_value': 0.5,
            'reason': '테스트 추천',
            'status': 'success',
            'result_pct': 2.0,
            'highest_price': 110.0,
        }
        values.update(overrides)
        return DailyRecommendation.objects.create(**values)

    def setUp(self):
        today = timezone.localdate()
        self._recommendation()
        self._recommendation(
            date=today - timedelta(days=1),
            coin_ticker='KRW-XRP',
            coin_name='XRP',
            status='failed',
            result_pct=-1.5,
            highest_price=101.0,
        )
        self._recommendation(
            date=today - timedelta(days=2),
            coin_ticker='KRW-ETH',
            coin_name='ETH',
            status='pending',
            result_pct=None,
            highest_price=None,
        )

    def test_search_and_status_filters_are_combined(self):
        response = self.client.get(reverse('stats_list'), {
            'q': 'btc',
            'status': 'success',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(
            [rec.coin_ticker for rec in response.context['recommendations']],
            ['KRW-BTC'],
        )
        self.assertContains(response, '최대 +10.00%')

    def test_date_range_filter(self):
        today = timezone.localdate()
        response = self.client.get(reverse('stats_list'), {
            'date_from': (today - timedelta(days=1)).isoformat(),
            'date_to': today.isoformat(),
        })

        self.assertEqual(response.context['total'], 2)
        self.assertEqual(response.context['wins'], 1)
        self.assertEqual(response.context['losses'], 1)
        self.assertEqual(response.context['win_rate'], 50.0)

    def test_invalid_filters_are_ignored_safely(self):
        response = self.client.get(reverse('stats_list'), {
            'status': 'not-a-status',
            'date_from': 'invalid-date',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 3)
        self.assertEqual(response.context['selected_status'], '')
        self.assertEqual(response.context['date_from'], '')

    def test_trade_type_filter_distinguishes_danta_and_swing(self):
        self._recommendation(
            trade_type='swing',
            status='pending',
            result_pct=None,
            highest_price=None,
        )

        response = self.client.get(reverse('stats_list'), {
            'trade_type': 'swing',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        recommendation = response.context['recommendations'][0]
        self.assertEqual(recommendation.trade_type, 'swing')
        self.assertContains(response, '스윙')

    def test_existing_records_default_to_danta(self):
        recommendation = DailyRecommendation.objects.get(coin_ticker='KRW-BTC')
        self.assertEqual(recommendation.trade_type, 'danta')

    def test_swing_page_is_available_before_strategy_is_implemented(self):
        response = self.client.get(reverse('swing_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '스윙 전략 준비 중')
        self.assertContains(response, '실제 추천을 생성하지 않습니다')

    def test_max_profit_property_uses_highest_observed_price(self):
        recommendation = DailyRecommendation.objects.get(coin_ticker='KRW-BTC')
        self.assertAlmostEqual(recommendation.max_profit_pct, 10.0)

    def test_pre_entry_observation_is_not_reported_as_max_profit(self):
        recommendation = DailyRecommendation.objects.get(coin_ticker='KRW-ETH')
        recommendation.highest_price = 99.0
        recommendation.save()

        self.assertIsNone(recommendation.max_profit_pct)


class RecommendationMonitorTestCase(TestCase):
    def _recommendation(self, **overrides):
        values = {
            'date': timezone.localdate(),
            'coin_ticker': 'KRW-BTC',
            'coin_name': 'BTC',
            'entry_price': 100.0,
            'target_price': 102.0,
            'stop_loss': 98.5,
            'k_value': 0.5,
            'reason': '테스트 추천',
            'status': 'success',
            'result_pct': 2.0,
            'highest_price': 102.0,
        }
        values.update(overrides)
        return DailyRecommendation.objects.create(**values)

    @patch('coinscreener.screener.management.commands.update_upbit_cache.time.sleep')
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_current_price',
        return_value=103.0,
    )
    def test_successful_pick_keeps_tracking_intraminute_high(
        self, _mock_current, mock_ohlcv, _mock_sleep
    ):
        recommendation = self._recommendation()
        mock_ohlcv.return_value = pd.DataFrame([{
            'open': 103.0,
            'high': 108.0,
            'low': 102.5,
            'close': 107.0,
        }])

        Command()._monitor_recommendations()

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'success')
        self.assertEqual(recommendation.highest_price, 108.0)
        self.assertAlmostEqual(recommendation.max_profit_pct, 8.0)

    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_current_price'
    )
    def test_danta_monitor_ignores_swing_records(self, mock_current):
        self._recommendation(
            trade_type='swing',
            status='active',
            result_pct=None,
        )

        Command()._monitor_recommendations()

        mock_current.assert_not_called()

    @patch.object(Command, '_monitor_recommendations')
    def test_independent_monitor_loop_runs_and_stops_cleanly(self, mock_monitor):
        stop_event = threading.Event()
        mock_monitor.side_effect = stop_event.set

        Command()._monitor_loop(stop_event)

        mock_monitor.assert_called_once_with()
