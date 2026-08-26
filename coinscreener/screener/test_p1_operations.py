import os
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .freshness import scan_freshness
from .models import AlertSetting, Condition, OHLCVCache, Strategy
from .scheduled_scans import run_scheduled_scans


def _cache_payload():
    return {
        'columns': ['open', 'high', 'low', 'close', 'volume'],
        'index': [1],
        'data': [[1, 1, 1, 1, 1]],
    }


class P1FreshnessTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name='P1 test')
        self.condition = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            left_indicator='CLOSE',
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )

    def test_fresh_cache_is_accepted(self):
        OHLCVCache.objects.create(
            ticker='KRW-BTC', timeframe='day', data=_cache_payload(),
        )
        result = scan_freshness(
            [{'ticker': 'KRW-BTC'}], [self.condition],
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['fresh_ratio'], 100.0)

    def test_stale_cache_is_rejected(self):
        cache = OHLCVCache.objects.create(
            ticker='KRW-BTC', timeframe='day', data=_cache_payload(),
        )
        OHLCVCache.objects.filter(pk=cache.pk).update(
            updated_at=timezone.now() - timedelta(days=2),
        )
        result = scan_freshness(
            [{'ticker': 'KRW-BTC'}], [self.condition],
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['stale_or_missing'], 1)


class P1ScheduledScanTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name='KOR')
        self.condition = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            left_indicator='CLOSE',
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )
        self.setting = AlertSetting.objects.create(
            strategy=self.strategy,
            enabled=True,
            alert_hour=10,
            alert_min=0,
            exchange='kospi',
            vol_limit=0,
        )
        OHLCVCache.objects.create(
            ticker='005930', timeframe='day', data=_cache_payload(),
        )

    @patch('coinscreener.screener.scheduled_scans.tg.is_configured', return_value=False)
    @patch('coinscreener.screener.scheduled_scans.process_scan_and_alert', return_value=([], []))
    @patch('coinscreener.screener.scheduled_scans._get_tickers', return_value=[{'ticker': '005930'}])
    def test_due_scan_runs_outside_http_request(
        self, _tickers, scan, _configured
    ):
        now = timezone.make_aware(datetime(2026, 8, 26, 10, 0))
        result = run_scheduled_scans(now_kst=now, output=None)
        self.assertTrue(result['ok'])
        self.assertEqual(result['processed'], 1)
        scan.assert_called_once()

    @patch('coinscreener.screener.scheduled_scans.tg.is_configured', return_value=False)
    @patch('coinscreener.screener.scheduled_scans.process_scan_and_alert')
    @patch('coinscreener.screener.scheduled_scans._get_tickers', return_value=[{'ticker': '005930'}])
    def test_stale_scheduled_scan_is_blocked(
        self, _tickers, scan, _configured
    ):
        OHLCVCache.objects.update(
            updated_at=timezone.now() - timedelta(days=2),
        )
        now = timezone.make_aware(datetime(2026, 8, 26, 10, 0))
        result = run_scheduled_scans(now_kst=now, output=None)
        self.assertFalse(result['ok'])
        self.assertIn('시세 캐시 지연', result['warnings'][0])
        scan.assert_not_called()

    @patch('coinscreener.screener.views.cron_views.process_scan_and_alert')
    def test_legacy_cron_endpoint_is_lightweight(self, scan):
        with patch.dict(os.environ, {'CRON_SECRET': 'test-secret'}):
            response = self.client.get(
                reverse('cron_scan'),
                HTTP_AUTHORIZATION='Bearer test-secret',
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['disabled'])
        scan.assert_not_called()


class P1HealthEndpointTestCase(TestCase):
    @patch('coinscreener.screener.views.api_views.collect_health')
    def test_health_endpoint_returns_503_for_critical_state(self, collect):
        collect.return_value = {
            'status': 'critical',
            'checked_at': '2026-08-26T10:00:00+09:00',
            'database_ok': True,
            'cache_age_minutes': 90,
        }
        response = self.client.get(reverse('healthz'))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['ok'])

    @patch('coinscreener.screener.management.commands.monitor_health.tg.send_message')
    @patch('coinscreener.screener.management.commands.monitor_health.tg.is_configured', return_value=True)
    @patch('coinscreener.screener.management.commands.monitor_health.collect_health')
    def test_health_monitor_notifies_only_on_state_transition(
        self, collect, _configured, send_message
    ):
        collect.return_value = {
            'status': 'critical',
            'checked_at': '2026-08-26T10:00:00+09:00',
            'database_ok': True,
            'web_ok': False,
            'cache_age_minutes': 90,
            'memory': {'available_pct': 20},
            'disk_free_pct': 50,
            'problems': ['Gunicorn 응답 없음'],
            'warnings': [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with override_settings(BASE_DIR=Path(temporary)):
                call_command('monitor_health', stdout=StringIO())
                call_command('monitor_health', stdout=StringIO())
        send_message.assert_called_once()


class P1AlertDefaultTimeTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name='KOSPI time')
        session = self.client.session
        session['owner_key'] = 'owner-test'
        session.save()
        self.strategy.owner_key = 'owner-test'
        self.strategy.save(update_fields=['owner_key'])

    def test_kospi_alert_defaults_to_ten_kst(self):
        response = self.client.post(
            reverse('alert_save', args=[self.strategy.id]),
            data='{"enabled": true, "exchange": "kospi", "vol_limit": 0}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        setting = AlertSetting.objects.get(strategy=self.strategy)
        self.assertEqual((setting.alert_hour, setting.alert_min), (10, 0))

