from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Condition, OHLCVCache, Strategy


class FastScanResultCacheTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name='빠른 조회 테스트')
        Condition.objects.create(
            strategy=self.strategy, timeframe='month', left_indicator='CLOSE',
            operator='gte', right_indicator='MA', right_param=10,
        )
        self.cache_key = f'strategy_results_{self.strategy.id}_upbit_0'

    def _save_result(self, completed_at):
        OHLCVCache.objects.create(
            ticker=self.cache_key, timeframe='RESULT',
            data={'results': [], 'last_updated': completed_at.isoformat()},
        )

    def test_recent_result_skips_another_full_scan(self):
        self._save_result(timezone.now())

        response = self.client.get(reverse('coin_search', args=[self.strategy.id]))

        self.assertRedirects(
            response,
            f'{reverse("coin_search_results", args=[self.strategy.id])}'
            '?exchange=upbit&vol_limit=0&cached=1',
            fetch_redirect_response=False,
        )

    def test_refresh_request_and_expired_result_open_scan_loader(self):
        self._save_result(timezone.now() - timedelta(minutes=6))
        expired = self.client.get(reverse('coin_search', args=[self.strategy.id]))
        refreshed = self.client.get(
            reverse('coin_search', args=[self.strategy.id]) + '?refresh=1',
        )

        self.assertTemplateUsed(expired, 'screener/search_loading.html')
        self.assertTemplateUsed(refreshed, 'screener/search_loading.html')
