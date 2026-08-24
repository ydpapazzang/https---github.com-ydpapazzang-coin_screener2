from datetime import timedelta
from unittest.mock import patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from filelock import FileLock

from .management.commands.generate_swing_picks import Command
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

    def _candidate(self, ticker='KRW-BTC'):
        return {
            'ticker': ticker,
            'name': ticker.replace('KRW-', ''),
            'entry_price': 100.0,
            'target_price': 110.0,
            'stop_loss': 95.0,
            'entry_expires_on': timezone.localdate() + timedelta(days=2),
            'reason': '테스트 스윙 추천',
        }

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
        'coinscreener.screener.management.commands.generate_swing_picks.pyupbit.get_ohlcv'
    )
    def test_process_lock_blocks_second_generator(self, mock_ohlcv):
        command = Command()
        outer_lock = FileLock(str(command._generation_lock_path()), timeout=0)

        with outer_lock:
            call_command('generate_swing_picks', '--force')

        mock_ohlcv.assert_not_called()
        self.assertFalse(
            DailyRecommendation.objects.filter(trade_type='swing').exists()
        )

    def test_persist_recommendations_saves_complete_batch(self):
        today = timezone.localdate()
        recommendations = [
            self._candidate('KRW-BTC'),
            self._candidate('KRW-ETH'),
        ]

        created = Command()._persist_recommendations(today, recommendations)

        self.assertTrue(created)
        self.assertEqual(
            list(
                DailyRecommendation.objects.filter(
                    date=today,
                    trade_type='swing',
                ).order_by('coin_ticker').values_list('coin_ticker', flat=True)
            ),
            ['KRW-BTC', 'KRW-ETH'],
        )

    def test_persist_recommendations_rolls_back_partial_failure(self):
        today = timezone.localdate()
        recommendations = [
            self._candidate('KRW-BTC'),
            self._candidate('KRW-ETH'),
        ]

        def partial_then_fail(objects):
            first = objects[0]
            DailyRecommendation.objects.create(
                date=first.date,
                trade_type=first.trade_type,
                coin_ticker=first.coin_ticker,
                coin_name=first.coin_name,
                entry_price=first.entry_price,
                target_price=first.target_price,
                stop_loss=first.stop_loss,
                initial_stop_loss=first.initial_stop_loss,
                entry_expires_on=first.entry_expires_on,
                k_value=first.k_value,
                reason=first.reason,
                status=first.status,
            )
            raise RuntimeError('write failed')

        with patch.object(
            DailyRecommendation.objects,
            'bulk_create',
            side_effect=partial_then_fail,
        ):
            with self.assertRaisesRegex(RuntimeError, 'write failed'):
                Command()._persist_recommendations(today, recommendations)

        self.assertFalse(
            DailyRecommendation.objects.filter(
                date=today,
                trade_type='swing',
            ).exists()
        )

    def test_persist_recommendations_rechecks_existing_result(self):
        today = timezone.localdate()
        DailyRecommendation.objects.create(
            date=today,
            trade_type='swing',
            coin_ticker='SKIP',
            coin_name='스윙휴식',
            entry_price=0,
            target_price=0,
            stop_loss=0,
            initial_stop_loss=0,
            k_value=0,
            reason='이미 저장됨',
            status='skipped',
        )

        created = Command()._persist_recommendations(
            today,
            [self._candidate('KRW-BTC')],
        )

        self.assertFalse(created)
        self.assertEqual(
            DailyRecommendation.objects.filter(
                date=today,
                trade_type='swing',
            ).count(),
            1,
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
        _mock_telegram
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
