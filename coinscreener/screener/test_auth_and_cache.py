import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pandas as pd

from .models import Strategy, Condition, UserProfile, OHLCVCache
from .engine import check_strategy, clear_all_screener_caches, get_max_required_len

User = get_user_model()


class PrivateAccessAndAuthTestCase(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_superuser(
            username='admin',
            email='admin@test.com',
            password='password123',
        )
        self.approved_user = User.objects.create_user(
            username='approved_user',
            email='approved@test.com',
            password='password123',
        )
        UserProfile.objects.create(user=self.approved_user, is_approved=True)

        self.pending_user = User.objects.create_user(
            username='pending_user',
            email='pending@test.com',
            password='password123',
        )
        UserProfile.objects.create(user=self.pending_user, is_approved=False)

    @override_settings(PRIVATE_ACCESS_ENABLED=True)
    def test_unauthenticated_user_redirected_to_login(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    @override_settings(PRIVATE_ACCESS_ENABLED=True)
    def test_whitelisted_paths_accessible_without_login(self):
        login_resp = self.client.get(reverse('login'))
        self.assertEqual(login_resp.status_code, 200)

        healthz_resp = self.client.get(reverse('healthz'))
        # 로그인 리다이렉트(302) 없이 화이트리스트로 바로 응답함
        self.assertNotEqual(healthz_resp.status_code, 302)

    @override_settings(PRIVATE_ACCESS_ENABLED=True)
    def test_unapproved_user_redirected_to_pending(self):
        self.client.force_login(self.pending_user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('auth_pending'), response.url)

        # /auth/pending/ 자체는 접근 가능
        pending_resp = self.client.get(reverse('auth_pending'))
        self.assertEqual(pending_resp.status_code, 200)
        self.assertContains(pending_resp, '관리자 승인 대기')

    @override_settings(PRIVATE_ACCESS_ENABLED=True)
    def test_approved_user_allowed(self):
        self.client.force_login(self.approved_user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    @override_settings(PRIVATE_ACCESS_ENABLED=True)
    def test_staff_user_allowed_everywhere(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('manage_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_toggle_user_approval(self):
        self.client.force_login(self.staff_user)
        # 승인 토글: pending -> approved
        url = reverse('toggle_user_approval', args=[self.pending_user.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['is_approved'])

        profile = UserProfile.objects.get(user=self.pending_user)
        self.assertTrue(profile.is_approved)

        # 다시 토글: approved -> pending
        resp2 = self.client.post(url)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertFalse(data2['is_approved'])

    def test_cannot_revoke_own_approval(self):
        self.client.force_login(self.staff_user)
        url = reverse('toggle_user_approval', args=[self.staff_user.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 400)


class RealtimePriceInjectionAndCacheTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name="MA Cross Up Test")
        self.cond = Condition.objects.create(
            strategy=self.strategy,
            timeframe='minute240',
            left_indicator='CLOSE',
            left_param=0,
            operator='cross_up',
            right_indicator='MA',
            right_param=5,
            offset=0,
        )

    def test_get_max_required_len_capping(self):
        # MA(200) + cross_up 1 extra = 201 should be capped to 200
        cond200 = Condition(
            left_indicator='CLOSE', left_param=0,
            operator='cross_up',
            right_indicator='MA', right_param=200,
            offset=0,
        )
        self.assertEqual(get_max_required_len([cond200]), 200)

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_live_price_injection_triggers_match(self, mock_get_ohlcv):
        # 5봉짜리 캔들 데이터 생성: 종가가 10, 10, 10, 10, 10 (MA5 = 10)
        dates = pd.date_range('2026-09-22 00:00', periods=6, freq='4h')
        df = pd.DataFrame({
            'open': [10.0] * 6,
            'high': [10.0] * 6,
            'low': [10.0] * 6,
            'close': [10.0] * 6,
            'volume': [100.0] * 6,
        }, index=dates)
        mock_get_ohlcv.return_value = df

        # 이전 봉까지는 MA(5)=10이고 종가=10 (돌파 아님)
        # 1) 현재가 = 9.0 (하락): 돌파 실패
        is_match, _, _, _, _, _ = check_strategy(
            'KRW-TEST', [self.cond], current_price=9.0, exchange='upbit'
        )
        self.assertFalse(is_match)

        # 2) 현재가 = 15.0 (급등 상향 돌파): 돌파 성공!
        is_match, _, _, _, _, _ = check_strategy(
            'KRW-TEST', [self.cond], current_price=15.0, exchange='upbit'
        )
        self.assertTrue(is_match)

    def test_clear_all_screener_caches(self):
        OHLCVCache.objects.create(
            ticker='TEST', timeframe='RESULT', data={'results': []}
        )
        self.assertEqual(OHLCVCache.objects.filter(timeframe='RESULT').count(), 1)
        clear_all_screener_caches()
        self.assertEqual(OHLCVCache.objects.filter(timeframe='RESULT').count(), 0)
