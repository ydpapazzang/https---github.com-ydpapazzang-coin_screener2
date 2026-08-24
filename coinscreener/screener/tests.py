import os
from django.test import TestCase, TransactionTestCase
from unittest.mock import patch
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from .models import Strategy, Condition
from .backtest import run_backtest
from .engine import check_strategy

class BacktestOffsetTestCase(TestCase):
    def setUp(self):
        # 1. 테스트용 전략 생성
        self.strategy = Strategy.objects.create(name="Test Strategy")
        
        # 2. 모킹용 OHLCV 데이터셋 생성 (300개 봉)
        # 149번째 봉까지는 종가 100원, 150번째 봉에서 150원으로 급등하여 유지되는 데이터 생성
        # 이렇게 하면 150번째 봉(index 150)에서 5일 이동평균(MA5) 대비 종가(CLOSE)가 급격히 커지는 조건 충족
        dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(300)]
        closes = [100.0] * 150 + [150.0] * 150
        opens = [100.0] * 300
        highs = [100.0] * 150 + [150.0] * 150
        lows = [100.0] * 300
        volumes = [1000.0] * 300
        values = [100000.0] * 300
        
        self.mock_df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
            'value': values
        }, index=dates)

    @patch('pyupbit.get_ohlcv')
    def test_backtest_respects_offset_zero(self, mock_get_ohlcv):
        """cond.offset = 0 (현재 봉 기준)일 때 백테스팅이 정확한 봉에서 매수 진입하는지 테스트"""
        mock_get_ohlcv.return_value = self.mock_df
        
        # 조건 설정: 종가(CLOSE, 0) > 단순이동평균(MA, 5)
        # offset = 0 (당일 종가 기준 바로 비교)
        cond = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='MA',
            right_param=5
        )
        
        # 백테스트 실행 (익절/손절 100% 모드로 해서 조기 매도 방지하고 N봉 후 매도로 설정)
        result = run_backtest(
            ticker='KRW-BTC',
            conditions=[cond],
            candle_count=200,
            sell_mode='exit_n',
            sell_param=5,
            fee_pct=0.0
        )
        
        self.assertNotIn('error', result)
        trades = result['trades']
        self.assertTrue(len(trades) > 0, "진입한 거래 내역이 있어야 합니다.")
        
        # 첫 번째 진입 날짜 확인
        # index 150 (2026-01-01 + 150일 = 2026-05-31)에서 처음으로 종가(150)가 MA5(110)보다 커지므로 즉시 진입해야 함
        first_entry = trades[0]
        expected_date = (datetime(2026, 1, 1) + timedelta(days=150)).strftime('%Y-%m-%d')
        self.assertEqual(first_entry['entry_date'], expected_date, 
                         f"offset=0일 때는 급등한 당일({expected_date})에 매수 진입해야 하지만 {first_entry['entry_date']}에 진입했습니다.")

    @patch('pyupbit.get_ohlcv')
    def test_backtest_respects_offset_one(self, mock_get_ohlcv):
        """cond.offset = 1 (1봉 전 기준)일 때 백테스팅이 1봉 늦게 매수 진입하는지 테스트"""
        mock_get_ohlcv.return_value = self.mock_df
        
        # 조건 설정: 1봉 전 종가(CLOSE, 0) > 1봉 전 단순이동평균(MA, 5)
        # offset = 1 (어제 종가 기준 비교)
        cond = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=1,
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='MA',
            right_param=5
        )
        
        # 백테스트 실행
        result = run_backtest(
            ticker='KRW-BTC',
            conditions=[cond],
            candle_count=200,
            sell_mode='exit_n',
            sell_param=5,
            fee_pct=0.0
        )
        
        self.assertNotIn('error', result)
        trades = result['trades']
        self.assertTrue(len(trades) > 0, "진입한 거래 내역이 있어야 합니다.")
        
        # 첫 번째 진입 날짜 확인
        # index 150에서 조건이 처음 만족되므로, offset=1이 적용되면 1봉 뒤인 index 151 (2026-06-01)에 매수 진입해야 함
        first_entry = trades[0]
        expected_date = (datetime(2026, 1, 1) + timedelta(days=151)).strftime('%Y-%m-%d')
        self.assertEqual(first_entry['entry_date'], expected_date, 
                         f"offset=1일 때는 급등 다음 날({expected_date})에 매수 진입해야 하지만 {first_entry['entry_date']}에 진입했습니다.")

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_realtime_screener_matches_offset(self, mock_get_ohlcv):
        """실시간 스크리너에서도 offset=1 조건이 정확하게 매칭 동작하는지 검증"""
        # index 298에서 급등하도록 데이터를 수정하여 MA가 아직 따라잡지 못하게 만듦
        custom_df = self.mock_df.copy()
        custom_df.loc[custom_df.index[:298], 'close'] = 100.0
        custom_df.loc[custom_df.index[:298], 'high'] = 100.0
        custom_df.loc[custom_df.index[298:], 'close'] = 150.0
        custom_df.loc[custom_df.index[298:], 'high'] = 150.0
        
        mock_get_ohlcv.return_value = custom_df
        
        cond = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=1,
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='MA',
            right_param=5
        )
        
        # 최신 시점(index 299) 기준 1봉 전(index 298)은 종가 150원, MA5 110원으로 조건 충족(True)되어야 함
        is_match, details, price, volume, change_rate, status = check_strategy('KRW-BTC', [cond])
        self.assertTrue(is_match)

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_bollinger_dynamic_std(self, mock_get_ohlcv):
        """Condition 모델의 bb_std 값(1.0 vs 5.0)에 따라 볼린저 밴드 상단 값이 동적으로 계산되는지 검증"""
        # 50개 봉짜리 테스트 데이터 생성: 48개는 100원, 마지막 2개 봉(index 48, 49)은 120원
        # 이렇게 하면 백테스트 시 index 48에서 매수 진입하고, 마지막 index 49에서 매도 청산되어 거래 내역(trades)에 기록됩니다.
        dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(50)]
        closes = [100.0] * 48 + [120.0] * 2
        opens = [100.0] * 50
        highs = [100.0] * 48 + [120.0] * 2
        lows = [100.0] * 50
        volumes = [1000.0] * 50
        values = [100000.0] * 50
        
        test_df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
            'value': values
        }, index=dates)
        
        mock_get_ohlcv.return_value = test_df
        
        # 1. bb_std = 1.0 설정 전략
        # CLOSE(0) > BB_UPPER(20), bb_std=1.0
        # 현재 종가는 120원(마지막 index 49)이므로 BB_UPPER 보다 크다 -> 참(True)
        cond_std1 = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='BB_UPPER',
            right_param=20,
            bb_std=1.0
        )
        
        # 2. bb_std = 5.0 설정 전략
        # CLOSE(0) > BB_UPPER(20), bb_std=5.0
        # BB_UPPER = 101.0 + 5.0 * 4.35 = 122.75
        # 현재 종가는 120원 이므로 BB_UPPER 보다 작다 -> 거짓(False)
        cond_std5 = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='BB_UPPER',
            right_param=20,
            bb_std=5.0
        )
        
        # 실시간 스크리너 테스트
        # bb_std=1.0 조건 검사 -> True 기대
        is_match_1, _, _, _, _, _ = check_strategy('KRW-BTC', [cond_std1])
        self.assertTrue(is_match_1, "bb_std=1.0 일 때는 종가가 BB_UPPER를 돌파해야 합니다.")
        
        # bb_std=5.0 조건 검사 -> False 기대
        is_match_5, _, _, _, _, _ = check_strategy('KRW-BTC', [cond_std5])
        self.assertFalse(is_match_5, "bb_std=5.0 일 때는 종가가 BB_UPPER를 돌파하지 못해야 합니다.")
        
        # 백테스팅 엔진 테스트
        # bb_std=1.0 백테스트 -> 진입 기록(trades) 존재해야 함
        res_bt1 = run_backtest('KRW-BTC', [cond_std1], candle_count=30, sell_mode='exit_n', sell_param=2)
        self.assertNotIn('error', res_bt1)
        self.assertTrue(len(res_bt1.get('trades', [])) > 0)
        
        # bb_std=5.0 백테스트 -> 진입 기록(trades) 없어야 함
        res_bt5 = run_backtest('KRW-BTC', [cond_std5], candle_count=30, sell_mode='exit_n', sell_param=2)
        self.assertNotIn('error', res_bt5)
        self.assertEqual(len(res_bt5.get('trades', [])), 0)

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_ichimoku_indicators(self, mock_get_ohlcv):
        """일목균형표 지표들(전환선, 기준선, 선행스팬1, 선행스팬2, 후행스팬) 계산 및 스크리닝/백테스트 검증"""
        # 일목 선행스팬2 계산을 위해 최소 78봉 이상의 데이터가 필요하므로 100개 봉 데이터 생성
        dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(100)]
        closes = [100.0] * 100
        opens = [100.0] * 100
        # 전환선/기준선 돌파 테스트를 위해 특정 구간 고가를 높임
        highs = [100.0] * 95 + [150.0] * 5
        lows = [100.0] * 100
        volumes = [1000.0] * 100
        values = [100000.0] * 100

        test_df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
            'value': values
        }, index=dates)

        mock_get_ohlcv.return_value = test_df

        # 1. 전환선 >= 기준선 조건
        cond_tenkan_kijun = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='IC_TENKAN',
            left_param=9,
            operator='gte',
            right_indicator='IC_KIJUN',
            right_param=26
        )

        is_match, _, _, _, _, _ = check_strategy('KRW-BTC', [cond_tenkan_kijun])
        self.assertTrue(is_match, "전환선(9)이 기준선(26)보다 크거나 같아야 합니다.")

        # 2. 선행스팬1 vs 선행스팬2 조건
        cond_span = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='IC_SPAN_A',
            left_param=26,
            operator='gte',
            right_indicator='IC_SPAN_B',
            right_param=26
        )
        is_match_span, _, _, _, _, _ = check_strategy('KRW-BTC', [cond_span])
        self.assertTrue(is_match_span)

        # 3. 후행스팬 vs 26봉 전 종가 조건
        cond_chikou = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='IC_CHIKOU',
            left_param=0,
            operator='gte',
            right_indicator='IC_CHIKOU_REF',
            right_param=26
        )
        is_match_chikou, _, _, _, _, _ = check_strategy('KRW-BTC', [cond_chikou])
        self.assertTrue(is_match_chikou)

        # 4. 백테스트 구동 확인
        res_bt = run_backtest('KRW-BTC', [cond_tenkan_kijun], candle_count=10, sell_mode='exit_n', sell_param=2)
        self.assertNotIn('error', res_bt)

    def test_scan_limit_capping(self):
        """대량 코인 스캔 요청(vol_limit=0 또는 150 등) 시 자동으로 최대 80개로 안전하게 캡핑되는지 검증"""
        # 1. 조건 추가 필요 (조건이 없으면 strategy_detail로 리다이렉트되므로 조건 생성)
        Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='VAL',
            right_param=100
        )
        
        # 2. vol_limit = 0 (제한 없음) 요청 시 -> 그대로 0으로 유지되어 로딩 화면으로 렌더링되어야 함
        response = self.client.get(f'/strategy/{self.strategy.id}/search/?exchange=upbit&vol_limit=0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['vol_limit'], 0)
        
        # 3. vol_limit = 150 (80 초과 대형 스캔) 요청 시 -> 그대로 150이 유지되어야 함
        response = self.client.get(f'/strategy/{self.strategy.id}/search/?exchange=upbit&vol_limit=150')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['vol_limit'], 150)

        # 4. vol_limit = 50 (80 이하 정상) 요청 시 -> 그대로 50 유지되어야 함
        response = self.client.get(f'/strategy/{self.strategy.id}/search/?exchange=upbit&vol_limit=50')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['vol_limit'], 50)

    def test_cron_scan_forbidden(self):
        """보안 토큰 또는 크론 헤더 없이 크론 경로 진입 시 403 차단 검증"""
        response = self.client.get('/cron/scan/')
        self.assertEqual(response.status_code, 403)

    @patch.dict('os.environ', {'CRON_SECRET': 'test_cron_secret'})
    def test_cron_scan_success(self):
        """올바른 Bearer 토큰을 전달했을 때 크론 스캔이 성공하는지 검증"""
        response = self.client.get(
            '/cron/scan/',
            HTTP_AUTHORIZATION='Bearer test_cron_secret',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])

        # 인증 헤더와 함께 강제 실행 플래그를 전달해도 성공하는지 검증
        response = self.client.get(
            '/cron/scan/?force=true',
            HTTP_AUTHORIZATION='Bearer test_cron_secret',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])

    def test_url_shortening(self):
        """TinyURL API 모킹을 통한 단축 URL 생성 및 실패 시 원본 안전 폴백 기능 검증"""
        from .telegram import shorten_url
        
        # 1. 모킹을 이용한 정상 단축 URL 반환 검증
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.text = "https://tinyurl.com/mocked_short"
            
            result = shorten_url("https://my-screener-site.com/strategy/19/")
            self.assertEqual(result, "https://tinyurl.com/mocked_short")
            
        # 2. 타임아웃/오류 발생 시 원본으로 안전하게 폴백하는지 검증
        with patch('requests.get', side_effect=Exception("API Timeout")):
            result = shorten_url("https://my-screener-site.com/strategy/19/")
            self.assertEqual(result, "https://my-screener-site.com/strategy/19/")

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_volume_indicators(self, mock_get_ohlcv):
        """거래량 지표들(VOLUME, VOLUME_PREV, VOLUME_MA) 계산 및 스크리닝 검증"""
        dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(10)]
        closes = [100.0] * 10
        opens = [100.0] * 10
        highs = [100.0] * 10
        lows = [100.0] * 10
        # 이전봉 대비 150%+ 돌파를 검증하기 위해 마지막 봉(idx -1) 거래량을 200, 그 직전 봉(idx -2) 거래량을 100으로 설정
        # 평균 거래량(N=5) 대비 200%+ 돌파를 검증하기 위해 앞의 봉들 평균을 50, 마지막 봉 거래량을 200으로 설정
        volumes = [50.0] * 8 + [100.0] + [200.0]
        values = [100000.0] * 10

        test_df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
            'value': values
        }, index=dates)

        mock_get_ohlcv.return_value = test_df

        # 1. VOLUME >= VOLUME_PREV * 1.5 (이전봉 거래량 대비 150%)
        cond_prev = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='VOLUME',
            left_param=0,
            operator='gte',
            right_indicator='VOLUME_PREV',
            right_param=1,
            bb_std=1.5
        )

        from .engine import check_strategy
        is_match, details, last_price, volume, change_rate, status = check_strategy('KRW-BTC', [cond_prev])
        self.assertTrue(is_match)

        cond_prev.delete()

        # 2. VOLUME >= VOLUME_MA(5) * 2.0 (최근 5봉 평균 대비 200%)
        cond_ma = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='VOLUME',
            left_param=0,
            operator='gte',
            right_indicator='VOLUME_MA',
            right_param=5,
            bb_std=2.0
        )

        is_match, details, last_price, volume, change_rate, status = check_strategy('KRW-BTC', [cond_ma])
        self.assertTrue(is_match)


class BulkPrefetchFreshnessTestCase(TestCase):
    def setUp(self):
        import json
        from django.core.cache import cache
        from .models import OHLCVCache
        from .views import scan_views

        cache.clear()
        with scan_views._PARSED_OHLCV_LOCK:
            scan_views._PARSED_OHLCV.clear()

        self.strategy = Strategy.objects.create(name="Prefetch freshness")
        self.condition = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )
        dates = pd.date_range('2026-01-01', periods=60, freq='D')
        self.df = pd.DataFrame({
            'open': [100.0] * 60,
            'high': [110.0] * 60,
            'low': [90.0] * 60,
            'close': [105.0] * 60,
            'volume': [1000.0] * 60,
        }, index=dates)
        self.cache_row = OHLCVCache.objects.create(
            ticker='KRW-BTC',
            timeframe='day',
            data=json.loads(self.df.to_json(orient='split')),
        )

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_fresh_db_cache_is_loaded_without_live_refresh(self, mock_get_ohlcv):
        from django.core.cache import cache
        from .views.scan_views import _bulk_prefetch_ohlcv

        _bulk_prefetch_ohlcv(
            [{'ticker': 'KRW-BTC'}], [self.condition], exchange='upbit'
        )

        mock_get_ohlcv.assert_not_called()
        cached = cache.get('ohlcv_KRW-BTC_day_60')
        self.assertIsNotNone(cached)
        self.assertEqual(float(cached['close'].iloc[-1]), 105.0)

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_stale_db_cache_is_not_reused_and_live_refresh_runs(self, mock_get_ohlcv):
        from django.core.cache import cache
        from django.utils import timezone
        from .models import OHLCVCache
        from .views.scan_views import _bulk_prefetch_ohlcv

        OHLCVCache.objects.filter(pk=self.cache_row.pk).update(
            updated_at=timezone.now() - timedelta(days=2)
        )
        cache.set('ohlcv_KRW-BTC_day_60', self.df.copy(), 180)
        mock_get_ohlcv.return_value = self.df

        _bulk_prefetch_ohlcv(
            [{'ticker': 'KRW-BTC'}], [self.condition], exchange='upbit'
        )

        mock_get_ohlcv.assert_called_once_with(
            'KRW-BTC',
            'day',
            count=60,
            exchange='upbit',
            persist_db=False,
        )
        self.assertIsNone(cache.get('ohlcv_KRW-BTC_day_60'))

    def test_live_fetch_workers_persist_on_the_request_thread(self):
        import threading
        from .models import OHLCVCache
        from .views.scan_views import _bulk_prefetch_ohlcv

        OHLCVCache.objects.filter(pk=self.cache_row.pk).delete()
        caller_thread = threading.get_ident()
        fetch_threads = []
        write_threads = []

        def fake_fetch(*args, **kwargs):
            fetch_threads.append(threading.get_ident())
            return self.df

        def fake_save(*args, **kwargs):
            write_threads.append(threading.get_ident())

        with patch(
            'coinscreener.screener.engine.get_ohlcv_with_retry',
            side_effect=fake_fetch,
        ), patch(
            'coinscreener.screener.engine.save_ohlcv_cache',
            side_effect=fake_save,
        ):
            _bulk_prefetch_ohlcv(
                [{'ticker': 'KRW-BTC'}], [self.condition], exchange='upbit'
            )

        self.assertEqual(len(fetch_threads), 1)
        self.assertNotEqual(fetch_threads[0], caller_thread)
        self.assertEqual(write_threads, [caller_thread])


class OHLCVCachePersistenceTestCase(TestCase):
    def test_sqlite_lock_is_retried_with_backoff(self):
        from django.db.utils import OperationalError
        from .engine import save_ohlcv_cache
        from .models import OHLCVCache

        df = pd.DataFrame({
            'open': [1.0], 'high': [1.0], 'low': [1.0],
            'close': [1.0], 'volume': [1.0],
        }, index=pd.date_range('2026-01-01', periods=1, freq='D'))
        saved = object()

        with patch.object(
            OHLCVCache.objects,
            'update_or_create',
            side_effect=[
                OperationalError('database is locked'),
                OperationalError('database is locked'),
                (saved, False),
            ],
        ) as mock_update, patch(
            'coinscreener.screener.engine.time.sleep'
        ) as mock_sleep:
            result = save_ohlcv_cache('KRW-BTC', 'day', df)

        self.assertIs(result, saved)
        self.assertEqual(mock_update.call_count, 3)
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [0.05, 0.1])


class TelegramSearchAuthorizationTestCase(TestCase):
    def setUp(self):
        self.sample = Strategy.objects.create(name='Public sample')
        Condition.objects.create(
            strategy=self.sample,
            timeframe='day',
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )

    def test_public_sample_can_still_be_searched_without_telegram(self):
        response = self.client.get(
            f'/strategy/{self.sample.id}/search/?send_telegram=0'
        )
        self.assertEqual(response.status_code, 200)

    def test_public_sample_cannot_request_telegram_from_loading_route(self):
        response = self.client.get(
            f'/strategy/{self.sample.id}/search/?send_telegram=1'
        )
        self.assertEqual(response.status_code, 404)

    def test_public_sample_cannot_bypass_loading_route_via_sse(self):
        response = self.client.get(
            f'/strategy/{self.sample.id}/search-stream/?send_telegram=1'
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_request_telegram_via_sse(self):
        owner_key = 'test-owner-key'
        session = self.client.session
        session['owner_key'] = owner_key
        session.save()
        owned = Strategy.objects.create(name='Owned strategy', owner_key=owner_key)
        Condition.objects.create(
            strategy=owned,
            timeframe='day',
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )

        response = self.client.get(
            f'/strategy/{owned.id}/search-stream/?send_telegram=1'
        )
        self.assertEqual(response.status_code, 200)
        response.close()


class AlertDeduplicationTestCase(TransactionTestCase):
    def setUp(self):
        from .models import AlertHistory

        self.strategy = Strategy.objects.create(name='Dedup strategy')
        self.condition = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )
        self.AlertHistory = AlertHistory
        self.tickers = [{'ticker': 'KRW-BTC', 'name': '비트코인'}]

    def _scan(self):
        from .views.strategy_views import process_scan_and_alert

        match = (True, ['조건 충족'], 100.0, 1_000_000.0, 1.0, 'new')
        with patch(
            'coinscreener.screener.views.strategy_views._bulk_prefetch_ohlcv'
        ), patch(
            'coinscreener.screener.views.strategy_views.check_strategy',
            return_value=match,
        ):
            return process_scan_and_alert(
                self.strategy, self.tickers, [self.condition], exchange='upbit'
            )

    def test_recently_notified_symbol_is_suppressed_for_twelve_hours(self):
        self.AlertHistory.objects.create(
            strategy=self.strategy,
            symbol='KRW-BTC',
            price=100.0,
            is_notified=True,
        )

        results, telegram_results = self._scan()

        self.assertEqual(len(results), 1)
        self.assertEqual(telegram_results, [])
        newest = self.AlertHistory.objects.order_by('-created_at').first()
        self.assertFalse(newest.is_notified)

    def test_symbol_can_be_notified_again_after_twelve_hours(self):
        from django.utils import timezone

        history = self.AlertHistory.objects.create(
            strategy=self.strategy,
            symbol='KRW-BTC',
            price=100.0,
            is_notified=True,
        )
        self.AlertHistory.objects.filter(pk=history.pk).update(
            created_at=timezone.now() - timedelta(hours=13)
        )

        results, telegram_results = self._scan()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(telegram_results), 1)
        newest = self.AlertHistory.objects.order_by('-created_at').first()
        self.assertTrue(newest.is_notified)

    def test_alert_history_bulk_write_runs_on_request_thread(self):
        import threading
        from .views.strategy_views import process_scan_and_alert

        caller_thread = threading.get_ident()
        write_threads = []
        match = (True, ['조건 충족'], 100.0, 1_000_000.0, 1.0, 'new')

        def capture_bulk_create(rows):
            write_threads.append(threading.get_ident())
            return rows

        with patch(
            'coinscreener.screener.views.strategy_views._bulk_prefetch_ohlcv'
        ), patch(
            'coinscreener.screener.views.strategy_views.check_strategy',
            return_value=match,
        ), patch.object(
            self.AlertHistory.objects,
            'bulk_create',
            side_effect=capture_bulk_create,
        ):
            process_scan_and_alert(
                self.strategy, self.tickers, [self.condition], exchange='upbit'
            )

        self.assertEqual(write_threads, [caller_thread])


class CronAlertSlotTestCase(TestCase):
    def setUp(self):
        from .models import AlertSetting

        self.strategy = Strategy.objects.create(name='Scheduled strategy')
        Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            left_indicator='CLOSE',
            left_param=0,
            operator='gt',
            right_indicator='VAL',
            right_param=0,
        )
        self.setting = AlertSetting.objects.create(
            strategy=self.strategy,
            enabled=True,
            alert_hour=9,
            alert_min=0,
            exchange='upbit',
            vol_limit=10,
        )

    @patch.dict('os.environ', {'CRON_SECRET': 'slot-secret'})
    def test_same_schedule_slot_runs_only_once(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_now = datetime(2026, 8, 21, 9, 5, tzinfo=ZoneInfo('Asia/Seoul'))
        with patch('django.utils.timezone.now', return_value=fixed_now), patch(
            'coinscreener.screener.views.cron_views.process_scan_and_alert',
            return_value=([], []),
        ) as mock_process, patch(
            'coinscreener.screener.views.cron_views._get_tickers',
            return_value=[],
        ), patch(
            'coinscreener.screener.views.cron_views.tg.is_configured',
            return_value=True,
        ), patch(
            'coinscreener.screener.views.cron_views.tg.send_alert',
            return_value={'ok': True},
        ):
            first = self.client.get(
                '/cron/scan/', HTTP_AUTHORIZATION='Bearer slot-secret'
            )
            second = self.client.get(
                '/cron/scan/', HTTP_AUTHORIZATION='Bearer slot-secret'
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['processed'], 1)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['processed'], 0)
        self.assertEqual(mock_process.call_count, 1)

    @patch.dict('os.environ', {'CRON_SECRET': 'slot-secret'})
    def test_eight_thirty_does_not_run_nine_oclock_schedule(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_now = datetime(2026, 8, 21, 8, 30, tzinfo=ZoneInfo('Asia/Seoul'))
        with patch('django.utils.timezone.now', return_value=fixed_now), patch(
            'coinscreener.screener.views.cron_views.process_scan_and_alert'
        ) as mock_process:
            response = self.client.get(
                '/cron/scan/', HTTP_AUTHORIZATION='Bearer slot-secret'
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['processed'], 0)
        mock_process.assert_not_called()


class StrategyTradingViewsTestCase(TestCase):
    def setUp(self):
        from .models import Strategy
        self.strategy = Strategy.objects.create(
            name="Trading Strategy",
            win_rate=65.0,
            stop_loss=-5.0,
            take_profit=15.0,
            capital_pct=25
        )

    def test_strategy_trading_root_redirects(self):
        # Accessing trading root should redirect to the first strategy's detail view
        response = self.client.get('/trading/')
        self.assertRedirects(response, f'/strategy/{self.strategy.id}/')

    def test_strategy_trading_detail_view(self):
        # Accessing a specific strategy's page should render correctly (using the new dashboard)
        response = self.client.get(f'/strategy/{self.strategy.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'screener/strategy_trading.html')
        self.assertContains(response, "Trading Strategy")

    def test_save_risk_settings(self):
        # Saving risk settings via AJAX POST
        import json
        payload = {
            'stop_loss': -10.0,
            'take_profit': 30.0,
            'capital_pct': 40
        }
        response = self.client.post(
            f'/strategy/{self.strategy.id}/save-risk/',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])

        # Check database
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.stop_loss, -10.0)
        self.assertEqual(self.strategy.take_profit, 30.0)
        self.assertEqual(self.strategy.capital_pct, 40)

    def test_strategy_rename(self):
        # Renaming strategy via AJAX POST
        import json
        payload = {
            'name': 'New Strategy Name'
        }
        response = self.client.post(
            f'/strategy/{self.strategy.id}/rename/',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])

        # Check database
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.name, 'New Strategy Name')

    def test_strategy_rename_empty(self):
        # Renaming with empty name should fail
        import json
        payload = {
            'name': '   '
        }
        response = self.client.post(
            f'/strategy/{self.strategy.id}/rename/',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertFalse(data['ok'])
        self.assertEqual(data['error'], '전략 이름을 입력해주세요.')

    def test_condition_add_volume(self):
        # Adding a volume condition via POST
        payload = {
            'cond_type': 'VOLUME',
            'timeframe': 'week',
            'offset': 0,
            'operator': 'gte',
            'volume_target': 'prev',
            'volume_pct': 150
        }
        response = self.client.post(
            f'/strategy/{self.strategy.id}/condition/add/',
            data=payload
        )
        self.assertEqual(response.status_code, 302) # Redirects back to strategy_detail
        
        # Check database
        conditions = self.strategy.conditions.filter(left_indicator='VOLUME')
        self.assertEqual(len(conditions), 1)
        c = conditions[0]
        self.assertEqual(c.timeframe, 'week')
        self.assertEqual(c.offset, 0)
        self.assertEqual(c.left_indicator, 'VOLUME')
        self.assertEqual(c.right_indicator, 'VOLUME_PREV')
        self.assertEqual(c.bb_std, 1.5) # 150% -> 1.5
        self.assertEqual(c.get_volume_pct, 150)

    def test_condition_add_bb(self):
        # Adding a bollinger bands condition via POST
        payload = {
            'cond_type': 'BB',
            'timeframe': 'day',
            'offset': 1,
            'operator': 'lt',
            'bb_period': 20,
            'bb_target': 'BB_UPPER'
        }
        response = self.client.post(
            f'/strategy/{self.strategy.id}/condition/add/',
            data=payload
        )
        self.assertEqual(response.status_code, 302)
        
        # Check database
        conditions = self.strategy.conditions.filter(right_indicator='BB_UPPER')
        self.assertEqual(len(conditions), 1)
        c = conditions[0]
        self.assertEqual(c.timeframe, 'day')
        self.assertEqual(c.offset, 1)
        self.assertEqual(c.left_indicator, 'CLOSE')
        self.assertEqual(c.right_indicator, 'BB_UPPER')
        self.assertEqual(c.right_param, 20)
        self.assertEqual(c.bb_std, 2.0)

    @patch('coinscreener.screener.views.cron_views._get_tickers')
    @patch('coinscreener.screener.views.cron_views.check_strategy')
    def test_strategy_scan_count_no_conditions(self, mock_check_strategy, mock_get_tickers):
        response = self.client.get(f'/strategy/{self.strategy.id}/scan-count/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['count'], 0)
        mock_get_tickers.assert_not_called()

    @patch('coinscreener.screener.views.cron_views._get_tickers')
    @patch('coinscreener.screener.views.cron_views.check_strategy')
    @patch('coinscreener.screener.views.cron_views._bulk_prefetch_ohlcv')
    def test_strategy_scan_count_with_conditions(self, mock_bulk, mock_check_strategy, mock_get_tickers):
        # 1. 조건 추가
        cond_ma = Condition.objects.create(
            strategy=self.strategy,
            timeframe='day',
            offset=0,
            left_indicator='CLOSE',
            operator='gte',
            right_indicator='MA',
            right_param=5
        )

        mock_get_tickers.return_value = ['KRW-BTC', 'KRW-ETH']

        def side_effect(ticker, conditions):
            if ticker == 'KRW-BTC':
                return True, ['골든크로스'], 50000.0, 1000000000.0, 0.0, '진입 대기'
            return False, [], 3000.0, 50000000.0, 0.0, '진입 대기'
        mock_check_strategy.side_effect = side_effect

        response = self.client.get(f'/strategy/{self.strategy.id}/scan-count/?exchange=upbit&vol_limit=100')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['count'], 1)

        # Check cache
        from django.core.cache import cache
        cached_data = cache.get(f"strategy_results_{self.strategy.id}_upbit_100")
        self.assertIsNotNone(cached_data)
        self.assertEqual(len(cached_data['results']), 1)
        self.assertEqual(cached_data['results'][0]['symbol'], 'KRW-BTC')
        self.assertEqual(cached_data['results'][0]['price'], 50000.0)
        self.assertEqual(cached_data['results'][0]['details'], '골든크로스')
        self.assertEqual(cached_data['results'][0]['volume_display'], '10.0억')

class CronBearerAuthorizationTestCase(TestCase):
    @patch.dict('os.environ', {'CRON_SECRET': 'header-only-secret'})
    def test_query_string_secret_is_rejected_for_all_cron_endpoints(self):
        for path in (
            '/cron/scan/',
            '/cron/prefetch/',
        ):
            with self.subTest(path=path):
                response = self.client.get(f'{path}?secret=header-only-secret')
                self.assertEqual(response.status_code, 403)

    @patch.dict('os.environ', {'CRON_SECRET': 'header-only-secret'})
    @patch(
        'coinscreener.screener.views.scan_views._get_tickers',
        return_value=[],
    )
    def test_prefetch_accepts_bearer_token(self, _mock_get_tickers):
        response = self.client.get(
            '/cron/prefetch/',
            HTTP_AUTHORIZATION='Bearer header-only-secret',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])


class RemovedOperationalEndpointsTestCase(TestCase):
    def test_remote_pick_generators_are_not_routable(self):
        for path in (
            '/cron/daily-picks/',
            '/cron/swing-picks/',
        ):
            with self.subTest(path=path):
                response = self.client.get(
                    path,
                    HTTP_AUTHORIZATION='Bearer even-a-valid-secret',
                )
                self.assertEqual(response.status_code, 404)

    def test_remote_migration_endpoint_is_not_routable(self):
        response = self.client.post('/cron/migrate/?secret=even-a-valid-secret')
        self.assertEqual(response.status_code, 404)

    def test_remote_debug_endpoint_is_not_routable(self):
        response = self.client.get('/cron/debug/?secret=even-a-valid-secret')
        self.assertEqual(response.status_code, 404)
