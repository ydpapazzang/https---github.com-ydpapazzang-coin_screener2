from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from .models import DailyRecommendation
from .strategy_confidence import build_confidence_report


class StrategyConfidenceTestCase(TestCase):
    def _record(self, day, result, version='danta-v-current', regime='uptrend'):
        return DailyRecommendation.objects.create(
            date=day, trade_type='danta', coin_ticker=f'KRW-C{day:%m%d}{DailyRecommendation.objects.count()}',
            coin_name='TEST', entry_price=100, target_price=102,
            stop_loss=98.5, reason='test', status='closed',
            result_pct=result, strategy_version=version,
            market_regime={'label': regime},
        )

    def test_latest_version_is_not_mixed_with_legacy_or_old_version(self):
        today = date(2026, 8, 28)
        self._record(today - timedelta(days=3), 50, version='old')
        self._record(today - timedelta(days=2), -50, version='')
        self._record(today - timedelta(days=1), 2, version='current')

        report = build_confidence_report('danta', today=today)

        self.assertEqual(report['strategy_version'], 'current')
        self.assertEqual(report['sample_count'], 1)
        self.assertAlmostEqual(report['expectancy_pct'], 1.8)

    def test_latest_rest_day_selects_new_version_without_counting_as_trade(self):
        today = date(2026, 8, 28)
        self._record(today - timedelta(days=1), 2, version='old')
        DailyRecommendation.objects.create(
            date=today, trade_type='danta', coin_ticker='SKIP',
            coin_name='단타휴식', entry_price=0, target_price=0, stop_loss=0,
            reason='시장 필터', status='skipped', strategy_version='new',
        )

        report = build_confidence_report('danta', today=today)

        self.assertEqual(report['strategy_version'], 'new')
        self.assertEqual(report['sample_count'], 0)

    def test_metrics_include_cost_mdd_streak_period_and_regime(self):
        today = date(2026, 8, 28)
        self._record(today - timedelta(days=100), 2, regime='uptrend')
        self._record(today - timedelta(days=20), -1.5, regime='downtrend')
        self._record(today - timedelta(days=10), -1.5, regime='downtrend')
        self._record(today, 2, regime='uptrend')

        report = build_confidence_report('danta', today=today)

        self.assertEqual(report['sample_count'], 4)
        self.assertAlmostEqual(report['expectancy_pct'], 0.05)
        self.assertGreater(report['mdd_pct'], 0)
        self.assertEqual(report['max_loss_streak'], 2)
        self.assertEqual(report['periods'][30]['sample_count'], 3)
        self.assertEqual(report['periods'][90]['sample_count'], 3)
        self.assertEqual(report['periods'][365]['sample_count'], 4)
        self.assertEqual(report['regimes'][0]['sample_count'], 2)

    def test_grade_thresholds_are_conservative(self):
        today = date(2026, 8, 28)
        for index in range(29):
            self._record(today - timedelta(days=index), 2)
        self.assertEqual(
            build_confidence_report('danta', today=today)['grade'],
            '데이터 부족',
        )
        self._record(today - timedelta(days=29), 2)
        self.assertEqual(
            build_confidence_report('danta', today=today)['grade'],
            '관찰',
        )

    def test_stats_page_displays_both_strategy_reports(self):
        response = self.client.get(reverse('stats_list'))

        self.assertContains(response, '전략 신뢰도')
        self.assertContains(response, '단타')
        self.assertContains(response, '스윙')
        self.assertContains(response, '데이터 부족')

