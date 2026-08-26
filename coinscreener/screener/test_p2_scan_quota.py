import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Condition, ScanLease, ScanUsage, Strategy
from .scan_quota import consume_scan, get_scan_quota, grant_reward_credit


class ScanQuotaTestCase(TestCase):
    @override_settings(REWARDED_AD_UNIT_PATH='', SCAN_DAILY_FREE_LIMIT=2)
    def test_unconfigured_ad_records_usage_without_blocking(self):
        results = [consume_scan('meter-only') for _ in range(3)]

        self.assertTrue(all(result.get('consumed') for result in results))
        self.assertFalse(results[-1]['enforced'])
        self.assertEqual(results[-1]['scan_count'], 3)

    @override_settings(
        REWARDED_AD_UNIT_PATH='/1234/wonii/rewarded',
        SCAN_DAILY_FREE_LIMIT=2,
    )
    def test_third_daily_scan_requires_reward(self):
        first = consume_scan('limited')
        second = consume_scan('limited')
        third = consume_scan('limited')

        self.assertTrue(first['consumed'])
        self.assertTrue(second['consumed'])
        self.assertNotIn('consumed', third)
        self.assertFalse(third['allowed'])
        self.assertEqual(third['scan_count'], 2)

    @override_settings(
        REWARDED_AD_UNIT_PATH='/1234/wonii/rewarded',
        SCAN_DAILY_FREE_LIMIT=2,
    )
    def test_reward_credit_unlocks_exactly_one_scan(self):
        consume_scan('rewarded')
        consume_scan('rewarded')
        reward = grant_reward_credit('rewarded')
        unlocked = consume_scan('rewarded')
        blocked_again = consume_scan('rewarded')

        self.assertTrue(reward['granted'])
        self.assertTrue(unlocked['consumed'])
        self.assertNotIn('consumed', blocked_again)

    @override_settings(
        REWARDED_AD_UNIT_PATH='/1234/wonii/rewarded',
        SCAN_DAILY_FREE_LIMIT=2,
    )
    def test_usage_resets_on_next_kst_date(self):
        today = timezone.localdate()
        consume_scan('new-day', date=today)
        consume_scan('new-day', date=today)

        tomorrow = get_scan_quota('new-day', date=today + timedelta(days=1))

        self.assertTrue(tomorrow['allowed'])
        self.assertEqual(tomorrow['scan_count'], 0)

    def test_prune_command_removes_old_usage_and_expired_lease(self):
        ScanUsage.objects.create(
            owner_key='old-usage',
            date=timezone.localdate() - timedelta(days=31),
            scan_count=2,
        )
        ScanLease.objects.create(
            owner_key='old-lease',
            token='expired',
            acquired_at=timezone.now() - timedelta(hours=1),
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        call_command('prune_visitlogs', days=30, stdout=StringIO())

        self.assertFalse(ScanUsage.objects.filter(owner_key='old-usage').exists())
        self.assertFalse(ScanLease.objects.filter(owner_key='old-lease').exists())


@override_settings(
    REWARDED_AD_UNIT_PATH='/1234/wonii/rewarded',
    SCAN_DAILY_FREE_LIMIT=2,
)
class RewardEndpointTestCase(TestCase):
    def setUp(self):
        session = self.client.session
        session['owner_key'] = 'reward-owner'
        session.save()

    def test_challenge_requires_exhausted_quota(self):
        response = self.client.post(reverse('scan_reward_challenge'))
        self.assertEqual(response.status_code, 409)

    def test_nonce_can_be_granted_only_once(self):
        consume_scan('reward-owner')
        consume_scan('reward-owner')
        challenge = self.client.post(reverse('scan_reward_challenge')).json()

        first = self.client.post(
            reverse('scan_reward_grant'),
            data=json.dumps({'nonce': challenge['nonce']}),
            content_type='application/json',
        )
        replay = self.client.post(
            reverse('scan_reward_grant'),
            data=json.dumps({'nonce': challenge['nonce']}),
            content_type='application/json',
        )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()['quota']['granted'])
        self.assertEqual(replay.status_code, 403)


@override_settings(
    REWARDED_AD_UNIT_PATH='/1234/wonii/rewarded',
    SCAN_DAILY_FREE_LIMIT=2,
)
class QuotaProtectedStreamTestCase(TestCase):
    def setUp(self):
        self.owner_key = 'quota-stream-owner'
        session = self.client.session
        session['owner_key'] = self.owner_key
        session.save()
        self.strategy = Strategy.objects.create(
            name='quota strategy', owner_key=self.owner_key,
        )
        Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            left_indicator='CLOSE',
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )

    @patch('coinscreener.screener.views.scan_views._get_tickers')
    def test_exhausted_stream_stops_before_market_work(self, get_tickers):
        consume_scan(self.owner_key)
        consume_scan(self.owner_key)

        response = self.client.get(
            reverse('coin_search_stream', args=[self.strategy.id]),
        )
        chunks = [chunk.decode('utf-8') for chunk in response.streaming_content]
        payload = json.loads(chunks[0].removeprefix('data: ').strip())

        self.assertEqual(payload['code'], 'quota_exhausted')
        get_tickers.assert_not_called()
        self.assertFalse(
            ScanLease.objects.filter(owner_key=self.owner_key).exists()
        )

    def test_loading_page_renders_rewarded_configuration(self):
        consume_scan(self.owner_key)
        consume_scan(self.owner_key)

        response = self.client.get(
            reverse('coin_search', args=[self.strategy.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'securepubads.g.doubleclick.net')
        self.assertContains(response, '짧은 광고를 끝까지 보고 조회 1회 받기')

