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
        
        # 2. vol_limit = 0 (제한 없음) 요청 시 -> 80으로 캡핑되어 로딩 화면으로 렌더링되어야 함
        response = self.client.get(f'/strategy/{self.strategy.id}/search/?exchange=upbit&vol_limit=0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['vol_limit'], 80)
        
        # 3. vol_limit = 150 (80 초과 대형 스캔) 요청 시 -> 80으로 캡핑되어야 함
        response = self.client.get(f'/strategy/{self.strategy.id}/search/?exchange=upbit&vol_limit=150')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['vol_limit'], 80)

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
