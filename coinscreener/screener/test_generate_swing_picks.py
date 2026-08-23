from datetime import timedelta
from unittest.mock import patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import DailyRecommendation


class GenerateSwingPicksTestCase(TestCase):
    def _open_recommendation(self, ticker):
        return DailyRecommendation.objects.create(
            date=timezone.localdate(),
            trade_type='swing',
            coin_ticker=ticker,
            coin_name=ticker.replace('KRW-', ''),
            entry_price=100,
            target_price=110,
            stop_loss=95,
            initial_stop_loss=95,
            k_value=0,
            reason='테스트',
            status='active',
        )

    @patch(
        'coinscreener.screener.management.commands.generate_swing_picks.pyupbit.get_ohlcv'
    )
    def test_generator_stops_when_three_positions_are_open(self, mock_ohlcv):
        for ticker in ('KRW-BTC', 'KRW-ETH', 'KRW-XRP'):
            self._open_recommendation(ticker)

        call_command('generate_swing_picks', '--force')

        mock_ohlcv.assert_not_called()
        self.assertEqual(
            DailyRecommendation.objects.filter(
                trade_type='swing',
                status='active',
            ).count(),
            3,
        )

    @patch(
        'coinscreener.screener.management.commands.generate_swing_picks.send_message',
        create=True,
    )
    @patch(
        'coinscreener.screener.telegram.send_message',
        return_value={'ok': True},
    )
    @patch(
        'coinscreener.screener.management.commands.generate_swing_picks.build_swing_recommendation'
    )
    @patch(
        'coinscreener.screener.management.commands.generate_swing_picks.validate_btc_regime'
    )
    @patch(
        'coinscreener.screener.management.commands.generate_swing_picks.Command._safe_krw_tickers',
        return_value=['KRW-BTC'],
    )
    @patch(
        'coinscreener.screener.management.commands.generate_swing_picks.pyupbit.get_current_price',
        return_value=100.0,
    )
    @patch(
        'coinscreener.screener.management.commands.generate_swing_picks.pyupbit.get_ohlcv'
    )
    def test_generator_persists_swing_lifecycle_fields(
        self,
        mock_ohlcv,
        _mock_current,
        _mock_tickers,
        mock_regime,
        mock_build,
        _mock_telegram,
        _unused_local_send,
    ):
        today = timezone.localdate()
        mock_ohlcv.return_value = pd.DataFrame({
            'value': [100_000_000.0] * 21,
        })
        mock_regime.return_value = {
            'close': 110.0,
            'ema60': 100.0,
        }
        mock_build.return_value = {
            'ticker': 'KRW-BTC',
            'name': 'BTC',
            'entry_price': 100.0,
            'target_price': 110.0,
            'stop_loss': 95.0,
            'entry_expires_on': today + timedelta(days=2),
            'momentum20_pct': 10.0,
            'trend_strength_pct': 10.0,
            'atr_pct': 3.0,
            'median_value_20': 100_000_000.0,
            'entry_gap_pct': 0.0,
            'stop_distance_pct': 5.0,
            'reason': '테스트 스윙 추천',
        }

        call_command('generate_swing_picks', '--force')

        recommendation = DailyRecommendation.objects.get(
            trade_type='swing',
            coin_ticker='KRW-BTC',
        )
        self.assertEqual(recommendation.status, 'pending')
        self.assertEqual(recommendation.initial_stop_loss, 95.0)
        self.assertEqual(
            recommendation.entry_expires_on,
            today + timedelta(days=2),
        )
