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
            'trade_type': 'not-a-type',
            'date_from': 'invalid-date',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 3)
        self.assertEqual(response.context['selected_status'], '')
        self.assertEqual(response.context['selected_trade_type'], '')
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

    @patch(
        'coinscreener.screener.views.danta_views._display_date',
        return_value=timezone.localdate(),
    )
    def test_danta_page_excludes_swing_records(self, _mock_display_date):
        self._recommendation(
            trade_type='swing',
            status='pending',
            result_pct=None,
            highest_price=None,
        )

        response = self.client.get(reverse('danta_list'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['recommendations'])
        self.assertTrue(all(
            rec.trade_type == 'danta'
            for rec in response.context['recommendations']
        ))

    def test_existing_records_default_to_danta(self):
        recommendation = DailyRecommendation.objects.get(coin_ticker='KRW-BTC')
        self.assertEqual(recommendation.trade_type, 'danta')

    def test_swing_page_describes_live_strategy(self):
        DailyRecommendation.objects.create(
            date=timezone.localdate(),
            trade_type='swing',
            coin_ticker='KRW-XRP',
            coin_name='XRP',
            entry_price=100.0,
            target_price=110.0,
            stop_loss=95.0,
            initial_stop_loss=95.0,
            k_value=0,
            reason='스윙 표시 테스트',
            status='pending',
        )
        response = self.client.get(reverse('swing_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '일봉 추세 돌파')
        self.assertContains(response, '진입가 (20일 돌파)')
        self.assertContains(response, '1차 목표가 (2R·50%)')
        self.assertContains(response, 'EMA20·3ATR 추적')
        self.assertContains(response, '초기 손절가')
        self.assertContains(response, '1회 위험 한도 자산의 0.5%')

    def test_max_profit_property_uses_highest_observed_price(self):
        recommendation = DailyRecommendation.objects.get(coin_ticker='KRW-BTC')
        self.assertAlmostEqual(recommendation.max_profit_pct, 10.0)

    def test_pre_entry_observation_is_not_reported_as_max_profit(self):
        recommendation = DailyRecommendation.objects.get(coin_ticker='KRW-ETH')
        recommendation.highest_price = 99.0
        recommendation.save()

        self.assertIsNone(recommendation.max_profit_pct)


class RecommendationMonitorTestCase(TestCase):
    def _minute_frame(self, rows, end=None):
        end = end or timezone.localtime().replace(
            second=0,
            microsecond=0,
            tzinfo=None,
        )
        index = pd.date_range(end=end, periods=len(rows), freq='min')
        return pd.DataFrame(rows, index=index)

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
        mock_ohlcv.return_value = self._minute_frame([{
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

        Command()._monitor_danta_recommendations()

        mock_current.assert_not_called()

    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_current_price'
    )
    def test_expired_swing_signal_closes_without_price_request(self, mock_current):
        recommendation = self._recommendation(
            trade_type='swing',
            status='pending',
            result_pct=None,
            entry_expires_on=timezone.localdate() - timedelta(days=1),
        )

        Command()._monitor_swing_recommendations()

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'closed')
        self.assertEqual(recommendation.exit_reason, 'entry_expired')
        mock_current.assert_not_called()

    @patch('coinscreener.screener.management.commands.update_upbit_cache.time.sleep')
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_current_price',
        return_value=101.0,
    )
    def test_swing_target_records_half_exit(
        self, _mock_current, mock_ohlcv, _mock_sleep
    ):
        recommendation = self._recommendation(
            trade_type='swing',
            status='active',
            result_pct=None,
            highest_price=None,
            lowest_price=None,
            initial_stop_loss=98.5,
        )
        mock_ohlcv.side_effect = [
            self._minute_frame([{
                'open': 101.0,
                'high': 103.0,
                'low': 100.0,
                'close': 102.0,
            }]),
            None,
        ]

        Command()._monitor_swing_recommendations()

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'partial')
        self.assertEqual(
            recommendation.partial_exit_price,
            recommendation.target_price,
        )

    @patch('coinscreener.screener.management.commands.update_upbit_cache.time.sleep')
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_current_price',
        return_value=101.0,
    )
    def test_swing_same_candle_uses_conservative_stop_first(
        self, _mock_current, mock_ohlcv, _mock_sleep
    ):
        recommendation = self._recommendation(
            trade_type='swing',
            status='active',
            result_pct=None,
            highest_price=None,
            lowest_price=None,
            initial_stop_loss=98.5,
        )
        mock_ohlcv.return_value = self._minute_frame([{
            'open': 101.0,
            'high': 103.0,
            'low': 98.0,
            'close': 101.0,
        }])

        Command()._monitor_swing_recommendations()

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'failed')
        self.assertIsNone(recommendation.partial_exit_price)
        self.assertEqual(recommendation.exit_reason, 'stop_loss')

    @patch('coinscreener.screener.management.commands.update_upbit_cache.time.sleep')
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    def test_swing_replays_target_reached_during_monitor_gap(
        self, mock_ohlcv, _mock_sleep
    ):
        now = timezone.localtime().replace(second=0, microsecond=0)
        checkpoint = now - timedelta(minutes=3)
        recommendation = self._recommendation(
            trade_type='swing',
            status='active',
            result_pct=None,
            highest_price=101.0,
            lowest_price=99.0,
            initial_stop_loss=98.5,
            entered_at=now - timedelta(days=1),
            last_checked_at=checkpoint,
        )
        minute_end = now.replace(tzinfo=None)
        minute_rows = self._minute_frame([
            {'open': 100.0, 'high': 101.0, 'low': 99.5, 'close': 100.5},
            {'open': 100.5, 'high': 103.0, 'low': 100.0, 'close': 102.5},
            {'open': 102.5, 'high': 102.7, 'low': 100.5, 'close': 101.0},
        ], end=minute_end)
        mock_ohlcv.side_effect = [minute_rows, None]

        Command()._monitor_swing_recommendations()

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'partial')
        self.assertEqual(
            recommendation.partial_exit_price,
            recommendation.target_price,
        )
        self.assertEqual(
            timezone.localtime(recommendation.last_checked_at).replace(
                tzinfo=None
            ),
            minute_rows.index[-1].to_pydatetime(),
        )
        minute_call = mock_ohlcv.call_args_list[0]
        self.assertGreaterEqual(minute_call.kwargs['count'], 5)
        self.assertEqual(minute_call.kwargs['period'], 0.12)

    @patch('coinscreener.screener.management.commands.update_upbit_cache.time.sleep')
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    def test_swing_rechecks_in_progress_checkpoint_candle(
        self, mock_ohlcv, _mock_sleep
    ):
        now = timezone.localtime().replace(second=0, microsecond=0)
        recommendation = self._recommendation(
            trade_type='swing',
            status='pending',
            result_pct=None,
            entry_price=100.0,
            target_price=104.0,
            stop_loss=98.0,
            initial_stop_loss=98.0,
            last_checked_at=now,
        )
        first_version = self._minute_frame([{
            'open': 99.0,
            'high': 99.5,
            'low': 98.8,
            'close': 99.2,
        }], end=now.replace(tzinfo=None))
        completed_version = self._minute_frame([{
            'open': 99.0,
            'high': 101.0,
            'low': 98.8,
            'close': 100.5,
        }], end=now.replace(tzinfo=None))
        mock_ohlcv.side_effect = [
            first_version,
            completed_version,
            None,
        ]

        Command()._monitor_swing_recommendations()
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'pending')

        Command()._monitor_swing_recommendations()
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'active')
        self.assertEqual(
            timezone.localtime(recommendation.entered_at).replace(tzinfo=None),
            now.replace(tzinfo=None),
        )

    @patch('coinscreener.screener.management.commands.update_upbit_cache.time.sleep')
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv',
        return_value=None,
    )
    def test_swing_api_failure_does_not_advance_checkpoint(
        self, _mock_ohlcv, _mock_sleep
    ):
        checkpoint = timezone.localtime() - timedelta(minutes=5)
        recommendation = self._recommendation(
            trade_type='swing',
            status='active',
            result_pct=None,
            entered_at=timezone.localtime() - timedelta(days=1),
            last_checked_at=checkpoint,
        )

        Command()._monitor_swing_recommendations()

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.last_checked_at, checkpoint)

    @patch('coinscreener.screener.management.commands.update_upbit_cache.time.sleep')
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    def test_replayed_raised_stop_is_recorded_as_trailing_stop(
        self, mock_ohlcv, _mock_sleep
    ):
        now = timezone.localtime().replace(second=0, microsecond=0)
        recommendation = self._recommendation(
            trade_type='swing',
            status='active',
            result_pct=None,
            entry_price=100.0,
            target_price=110.0,
            stop_loss=99.0,
            initial_stop_loss=95.0,
            entered_at=now - timedelta(days=2),
            last_checked_at=now - timedelta(minutes=2),
        )
        mock_ohlcv.return_value = self._minute_frame([{
            'open': 100.0,
            'high': 100.5,
            'low': 98.5,
            'close': 99.5,
        }], end=now.replace(tzinfo=None))

        Command()._monitor_swing_recommendations()

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, 'failed')
        self.assertEqual(recommendation.exit_reason, 'trailing_stop')
        self.assertEqual(recommendation.exit_price, 99.0)

    @patch.object(Command, '_monitor_recommendations')
    def test_independent_monitor_loop_runs_and_stops_cleanly(self, mock_monitor):
        stop_event = threading.Event()
        mock_monitor.side_effect = stop_event.set

        Command()._monitor_loop(stop_event)

        mock_monitor.assert_called_once_with()

