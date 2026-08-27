from unittest.mock import patch

import pandas as pd
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .models import DailyRecommendation
from .recommendation_versioning import (
    current_code_version,
    json_number,
    recommendation_snapshot,
)


class RecommendationVersioningTestCase(SimpleTestCase):
    def tearDown(self):
        current_code_version.cache_clear()

    @override_settings(APP_COMMIT_SHA='1234567890abcdef')
    def test_configured_commit_has_priority(self):
        current_code_version.cache_clear()

        self.assertEqual(current_code_version(), '1234567890abcdef')

    @override_settings(APP_COMMIT_SHA='release-2026-08-27')
    def test_snapshot_contains_all_reproducibility_fields(self):
        current_code_version.cache_clear()
        observed_at = timezone.now()

        snapshot = recommendation_snapshot(
            'danta-breakout-v2.0',
            {'target_pct': 2.0},
            {'label': 'btc_intraday_filter_passed'},
            observed_at,
        )

        self.assertEqual(snapshot['strategy_version'], 'danta-breakout-v2.0')
        self.assertEqual(snapshot['strategy_parameters']['target_pct'], 2.0)
        self.assertEqual(
            snapshot['market_regime']['label'],
            'btc_intraday_filter_passed',
        )
        self.assertEqual(snapshot['data_as_of'], observed_at)
        self.assertEqual(snapshot['code_version'], 'release-2026-08-27')

    def test_json_number_rejects_non_finite_values(self):
        self.assertIsNone(json_number(float('nan')))
        self.assertIsNone(json_number(float('inf')))
        self.assertEqual(json_number('1.25'), 1.25)


class DailyRecommendationSnapshotTestCase(TestCase):
    @override_settings(APP_COMMIT_SHA='daily-test-commit')
    @patch(
        'coinscreener.screener.management.commands.generate_daily_picks.pyupbit.get_ohlcv'
    )
    def test_danta_rest_day_records_strategy_and_market_snapshot(
        self, mock_ohlcv
    ):
        current_code_version.cache_clear()
        mock_ohlcv.return_value = pd.DataFrame({
            'close': [float(200 - index) for index in range(100)],
        })

        call_command('generate_daily_picks', '--force')

        recommendation = DailyRecommendation.objects.get(
            trade_type='danta',
            coin_ticker='SKIP',
        )
        self.assertEqual(
            recommendation.strategy_version,
            'danta-breakout-v2.0',
        )
        self.assertEqual(
            recommendation.market_regime['label'],
            'btc_1h_downtrend',
        )
        self.assertEqual(
            recommendation.strategy_parameters['target_pct'],
            2.0,
        )
        self.assertEqual(recommendation.code_version, 'daily-test-commit')
        self.assertIsNotNone(recommendation.data_as_of)

