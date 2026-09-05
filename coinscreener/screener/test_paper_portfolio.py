from unittest.mock import patch

import pandas as pd
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .management.commands.update_upbit_cache import Command
from .models import DailyRecommendation, PaperPosition


class PaperPortfolioViewTestCase(TestCase):
    def setUp(self):
        self.recommendation = DailyRecommendation.objects.create(
            date=timezone.localdate(), trade_type='danta',
            coin_ticker='KRW-BTC', coin_name='BTC', entry_price=100,
            target_price=102, stop_loss=98.5, reason='test', status='pending',
        )

    def _owner(self, client, value):
        session = client.session
        session['owner_key'] = value
        session.save()

    def test_recommendation_can_be_added_with_actual_fill(self):
        self._owner(self.client, 'owner-a')
        response = self.client.post(
            reverse('portfolio_add', args=[self.recommendation.id]),
            {'entry_price': '100.5', 'invested_amount': '100000'},
        )

        self.assertRedirects(response, reverse('portfolio_list'))
        position = PaperPosition.objects.get()
        self.assertEqual(position.owner_key, 'owner-a')
        self.assertEqual(position.entry_price, 100.5)
        self.assertEqual(position.invested_amount, 100000)
        self.assertEqual(position.target_price, 102)

    def test_ajax_add_returns_redirect_instruction(self):
        self._owner(self.client, 'owner-a')
        response = self.client.post(
            reverse('portfolio_add', args=[self.recommendation.id]),
            {'entry_price': '100.5', 'invested_amount': '100000'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'ok': True,
            'redirect_url': reverse('portfolio_list'),
        })

    def test_positions_are_private_to_browser_session(self):
        PaperPosition.objects.create(
            owner_key='owner-a', recommendation=self.recommendation,
            trade_type='danta', coin_ticker='KRW-BTC', coin_name='BTC',
            entry_price=100, invested_amount=100000, target_price=102,
            stop_loss=98.5, current_price=101,
        )
        self._owner(self.client, 'owner-b')

        response = self.client.get(reverse('portfolio_list'))

        self.assertNotContains(response, 'KRW-BTC')

    def test_invalid_fill_outside_target_and_stop_is_rejected(self):
        self._owner(self.client, 'owner-a')
        response = self.client.post(
            reverse('portfolio_add', args=[self.recommendation.id]),
            {'entry_price': '103', 'invested_amount': '100000'},
        )

        self.assertContains(response, '손절가보다 높고 목표가보다 낮아야')
        self.assertFalse(PaperPosition.objects.exists())


class PaperPortfolioMonitorTestCase(TestCase):
    def _position(self):
        return PaperPosition.objects.create(
            owner_key='owner', trade_type='danta', coin_ticker='KRW-BTC',
            coin_name='BTC', entry_price=100, invested_amount=100000,
            target_price=102, stop_loss=98.5, current_price=100,
            highest_price=100, lowest_price=100,
        )

    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.time.sleep'
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    def test_target_is_closed_automatically(self, mock_ohlcv, _mock_sleep):
        position = self._position()
        mock_ohlcv.return_value = pd.DataFrame(
            [{'open': 100, 'high': 103, 'low': 99, 'close': 102.5}]
        )

        Command()._monitor_paper_positions()

        position.refresh_from_db()
        self.assertEqual(position.status, 'closed')
        self.assertEqual(position.exit_reason, 'target')
        self.assertEqual(position.exit_price, 102)
        self.assertIsNotNone(position.exit_at)

    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.time.sleep'
    )
    @patch(
        'coinscreener.screener.management.commands.update_upbit_cache.pyupbit.get_ohlcv'
    )
    def test_same_candle_prefers_stop_loss(self, mock_ohlcv, _mock_sleep):
        position = self._position()
        mock_ohlcv.return_value = pd.DataFrame(
            [{'open': 100, 'high': 103, 'low': 98, 'close': 101}]
        )

        Command()._monitor_paper_positions()

        position.refresh_from_db()
        self.assertEqual(position.exit_reason, 'stop_loss')
        self.assertEqual(position.exit_price, 98.5)

