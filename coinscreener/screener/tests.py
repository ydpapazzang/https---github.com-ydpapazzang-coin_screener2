from django.test import TestCase
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

    @patch('pyupbit.get_ohlcv')
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
        is_match, details, price, volume, status = check_strategy('KRW-BTC', [cond])
        self.assertTrue(is_match)

    @patch('pyupbit.get_ohlcv')
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
        is_match_1, _, _, _, _ = check_strategy('KRW-BTC', [cond_std1])
        self.assertTrue(is_match_1, "bb_std=1.0 일 때는 종가가 BB_UPPER를 돌파해야 합니다.")
        
        # bb_std=5.0 조건 검사 -> False 기대
        is_match_5, _, _, _, _ = check_strategy('KRW-BTC', [cond_std5])
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

    @patch('pyupbit.get_ohlcv')
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

        is_match, _, _, _, _ = check_strategy('KRW-BTC', [cond_tenkan_kijun])
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
        is_match_span, _, _, _, _ = check_strategy('KRW-BTC', [cond_span])
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
        is_match_chikou, _, _, _, _ = check_strategy('KRW-BTC', [cond_chikou])
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

    def test_ai_ask_fallback_when_no_api_key(self):
        """GROQ_API_KEY 환경변수가 없을 때 예시 폴백 응답이 반환되는지 확인"""
        with patch.dict('os.environ', {'GROQ_API_KEY': ''}):
            response = self.client.post('/ai/ask/', data={'prompt': '골든크로스 전략 알려줘'})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn('response', data)
            self.assertIn('API 키가 로컬 .env 또는 Vercel 환경 변수에 설정되어 있지 않습니다', data['response'])

    def test_ai_ask_empty_prompt(self):
        """빈 프롬프트 요청 시 400 에러를 반환하는지 확인"""
        response = self.client.post('/ai/ask/', data={'prompt': ''})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('error', data)

    def test_ai_strategy_create_success(self):
        """AI로 파싱한 JSON 데이터를 통해 실제 전략 및 조건식을 생성하는 API 검증"""
        import json
        payload = {
            "create_strategy": {
                "name": "골든크로스 전략",
                "conditions": [
                    {
                        "timeframe": "day",
                        "offset": 0,
                        "left_indicator": "CLOSE",
                        "left_param": 0,
                        "operator": "gt",
                        "right_indicator": "MA",
                        "right_param": 20
                    },
                    {
                        "timeframe": "minute60",
                        "offset": 1,
                        "left_indicator": "RSI",
                        "left_param": 14,
                        "operator": "lt",
                        "right_indicator": "VAL",
                        "right_param": 30
                    }
                ]
            }
        }
        
        response = self.client.post(
            '/ai/strategy/create/',
            data=json.dumps(payload),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertIn('strategy_id', data)
        self.assertIn('redirect_url', data)
        
        # 데이터베이스 생성 확인
        strategy = Strategy.objects.get(id=data['strategy_id'])
        self.assertEqual(strategy.name, "골든크로스 전략")
        
        conditions = strategy.conditions.all().order_by('id')
        self.assertEqual(len(conditions), 2)
        
        c1 = conditions[0]
        self.assertEqual(c1.timeframe, "day")
        self.assertEqual(c1.offset, 0)
        self.assertEqual(c1.left_indicator, "CLOSE")
        self.assertEqual(c1.left_param, 0)
        self.assertEqual(c1.operator, "gt")
        self.assertEqual(c1.right_indicator, "MA")
        self.assertEqual(c1.right_param, 20)
        
        c2 = conditions[1]
        self.assertEqual(c2.timeframe, "minute60")
        self.assertEqual(c2.offset, 1)
        self.assertEqual(c2.left_indicator, "RSI")
        self.assertEqual(c2.left_param, 14)
        self.assertEqual(c2.operator, "lt")
        self.assertEqual(c2.right_indicator, "VAL")
        self.assertEqual(c2.right_param, 30)

    def test_ai_strategy_create_invalid_methods(self):
        """GET 요청 등 부적절한 메소드로 전략 생성 API 접근 시 405 차단 확인"""
        response = self.client.get('/ai/strategy/create/')
        self.assertEqual(response.status_code, 405)
        
    def test_ai_strategy_create_invalid_data(self):
        """빈 데이터 또는 잘못된 구조의 JSON 전송 시 400 반환 검증"""
        import json
        response = self.client.post(
            '/ai/strategy/create/',
            data=json.dumps({"invalid_key": "dummy"}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)

    def test_cron_scan_forbidden(self):
        """보안 토큰 또는 크론 헤더 없이 크론 경로 진입 시 403 차단 검증"""
        response = self.client.get('/cron/scan/')
        self.assertEqual(response.status_code, 403)

    def test_cron_scan_success(self):
        """디버그 토큰을 전달하거나 Vercel Cron 헤더를 전달했을 때 크론 스캔이 성공적으로 수행되는지 검증"""
        # 1. 헤더 전달을 통한 성공 검증
        response = self.client.get('/cron/scan/', HTTP_X_VERCEL_CRON='1')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        
        # 2. 디버그 쿼리 토큰을 통한 성공 검증
        response = self.client.get('/cron/scan/?secret=wonii_cron_debug')
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

    @patch('pyupbit.get_ohlcv')
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
        is_match, details, last_price, volume, status = check_strategy('KRW-BTC', [cond_prev])
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

        is_match, details, last_price, volume, status = check_strategy('KRW-BTC', [cond_ma])
        self.assertTrue(is_match)


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
        self.assertContains(response, "승률 65%")

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



