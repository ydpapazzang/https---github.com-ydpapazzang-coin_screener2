import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Condition, ScanLease, Strategy
from .scan_guard import acquire_scan_lease, release_scan_lease


class ScanLeaseTestCase(TestCase):
    def test_only_one_active_lease_per_owner(self):
        first = acquire_scan_lease('owner-one')
        second = acquire_scan_lease('owner-one')

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertTrue(release_scan_lease('owner-one', first))
        self.assertIsNotNone(acquire_scan_lease('owner-one'))

    def test_expired_lease_can_be_reclaimed(self):
        ScanLease.objects.create(
            owner_key='owner-expired',
            token='old-token',
            acquired_at=timezone.now() - timedelta(hours=1),
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        token = acquire_scan_lease('owner-expired')

        self.assertIsNotNone(token)
        self.assertNotEqual(token, 'old-token')

    def test_wrong_token_cannot_release_new_lease(self):
        token = acquire_scan_lease('owner-protected')

        self.assertFalse(release_scan_lease('owner-protected', 'wrong-token'))
        self.assertTrue(
            ScanLease.objects.filter(owner_key='owner-protected', token=token).exists()
        )


class ScanStreamGuardTestCase(TestCase):
    def setUp(self):
        self.owner_key = 'stream-owner'
        session = self.client.session
        session['owner_key'] = self.owner_key
        session.save()
        self.strategy = Strategy.objects.create(
            name='guarded strategy', owner_key=self.owner_key,
        )
        Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            left_indicator='CLOSE',
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )

    def test_second_stream_receives_busy_event_without_scanning(self):
        token = acquire_scan_lease(self.owner_key)
        response = self.client.get(
            reverse('coin_search_stream', args=[self.strategy.id]),
        )

        first_chunk = next(iter(response.streaming_content)).decode('utf-8')
        payload = json.loads(first_chunk.removeprefix('data: ').strip())
        self.assertEqual(payload['type'], 'error')
        self.assertEqual(payload['code'], 'scan_busy')
        self.assertTrue(release_scan_lease(self.owner_key, token))

    def test_stream_releases_lease_when_work_finishes(self):
        self.strategy.conditions.all().delete()
        response = self.client.get(
            reverse('coin_search_stream', args=[self.strategy.id]),
        )

        list(response.streaming_content)

        self.assertFalse(
            ScanLease.objects.filter(owner_key=self.owner_key).exists()
        )

