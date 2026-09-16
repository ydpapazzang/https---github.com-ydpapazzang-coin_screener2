from django.test import SimpleTestCase, override_settings
from django.utils import timezone

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

