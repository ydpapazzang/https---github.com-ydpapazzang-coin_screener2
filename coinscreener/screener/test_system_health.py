from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from . import system_health
from .models import OHLCVCache


class MemoryHealthThresholdTestCase(TestCase):
    def setUp(self):
        OHLCVCache.objects.create(
            ticker='KRW-TEST', timeframe='minute5',
            data={'index': [], 'columns': [], 'data': []},
        )

    def test_temporary_monitor_memory_overhead_is_not_a_warning(self):
        """12~14%는 1GB VM에서 헬스체크 프로세스가 뜬 순간에도 발생한다."""
        memory = {
            'total_bytes': 1_000,
            'available_bytes': 124,
            'available_pct': 12.4,
            'swap_total_bytes': 1_000,
            'swap_used_bytes': 300,
        }
        with patch.object(system_health, '_linux_memory', return_value=memory), \
             patch.object(system_health, '_web_is_listening', return_value=True):
            snapshot = system_health.collect_health()

        self.assertEqual(snapshot['status'], 'ok')
        self.assertEqual(snapshot['warnings'], [])

    def test_low_available_memory_still_warns(self):
        memory = {
            'total_bytes': 1_000,
            'available_bytes': 95,
            'available_pct': 9.5,
            'swap_total_bytes': 1_000,
            'swap_used_bytes': 300,
        }
        with patch.object(system_health, '_linux_memory', return_value=memory), \
             patch.object(system_health, '_web_is_listening', return_value=True):
            snapshot = system_health.collect_health()

        self.assertEqual(snapshot['status'], 'warning')
        self.assertEqual(snapshot['warnings'], ['가용 메모리 9.5%'])
