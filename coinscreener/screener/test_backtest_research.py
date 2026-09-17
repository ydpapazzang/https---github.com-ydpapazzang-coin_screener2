import json
from unittest.mock import patch

import pandas as pd
from django.test import TestCase

from .backtest_research import (
    build_research_report, classify_market_regimes, monte_carlo,
    split_validation,
)
from .models import Condition, Strategy


def sample_trades(count=10):
    return [
        {'entry_date': f'2026-01-{index + 1:02d}',
         'exit_date': f'2026-01-{index + 2:02d}',
         'return_pct': 2 if index % 2 == 0 else -1}
        for index in range(count)
    ]


class BacktestResearchAnalysisTestCase(TestCase):
    def test_validation_is_chronological_and_disjoint(self):
        result = split_validation(sample_trades())
        self.assertEqual(result['train']['total_trades'], 7)
        self.assertEqual(result['validation']['total_trades'], 3)

    def test_monte_carlo_is_repeatable(self):
        first = monte_carlo(sample_trades(), simulations=50)
        second = monte_carlo(sample_trades(), simulations=50)
        self.assertEqual(first, second)

    def test_report_warns_about_small_sample(self):
        baseline = {'trades': sample_trades(), 'total_trades': 10,
                    'win_rate': 50, 'expectancy': .5, 'total_return': 5,
                    'mdd': 1, 'sharpe': 1}
        report = build_research_report(
            baseline, [{'label': '기준', 'result': baseline}], {}
        )
        self.assertIn(report['overfit']['level'], ('주의', '높음'))
        self.assertIn('30건 미만', report['overfit']['warnings'][0])

    def test_market_regime_classification(self):
        index = pd.date_range('2025-01-01', periods=100)
        frame = pd.DataFrame({'close': range(100, 200)}, index=index)
        regimes = classify_market_regimes(frame)
        self.assertIn('상승장', regimes.values())


class BacktestResearchViewTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name='월봉이', owner_key=None)
        Condition.objects.create(
            strategy=self.strategy, timeframe='day', left_indicator='CLOSE',
            left_param=0, operator='gt', right_indicator='VAL', right_param=0,
        )

    def test_lab_page_is_available_for_public_sample(self):
        response = self.client.get(f'/strategy/{self.strategy.id}/backtest/lab/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '백테스트 연구실')

    @patch('coinscreener.screener.views.research_views.pyupbit.get_ohlcv')
    @patch('coinscreener.screener.views.research_views.run_backtest')
    def test_lab_returns_all_research_sections(self, run_backtest, get_ohlcv):
        trades = sample_trades()
        run_backtest.return_value = {
            'trades': trades, 'total_trades': 10, 'win_rate': 50,
            'expectancy': .5, 'total_return': 5, 'mdd': 2, 'sharpe': 1,
        }
        get_ohlcv.return_value = pd.DataFrame(
            {'close': range(100, 200)},
            index=pd.date_range('2025-01-01', periods=100),
        )
        response = self.client.post(
            f'/strategy/{self.strategy.id}/backtest/lab/run/',
            data={'ticker': 'KRW-BTC', 'candle_count': 100,
                  'sell_mode': 'cond_exit', 'sell_param': 5,
                  'fee': .05, 'slippage': .05},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in ('validation', 'walk_forward', 'regimes', 'sensitivity',
                    'monte_carlo', 'overfit'):
            self.assertIn(key, body)

    @patch('coinscreener.screener.views.research_views.run_backtest')
    def test_timeframe_comparison_success(self, mock_backtest):
        def _mock_run(ticker, conditions, candle_count, sell_mode, sell_param, fee, slippage):
            tf = conditions[0].timeframe
            # Simulate different results per timeframe
            ret = 10.0 if tf == 'minute60' else 2.0
            return {
                'trades': [
                    {'return_pct': 4.0, 'entry_date': '2026-01-01', 'exit_date': '2026-01-02'},
                    {'return_pct': -2.0, 'entry_date': '2026-01-03', 'exit_date': '2026-01-04'},
                ],
                'total_trades': 2,
                'win_rate': 50.0,
                'total_return': ret,
                'mdd': 2.5,
            }
        mock_backtest.side_effect = _mock_run

        response = self.client.post(
            f'/strategy/{self.strategy.id}/backtest/timeframes/',
            data=json.dumps({
                'ticker': 'KRW-BTC',
                'candle_count': 200,
                'sell_mode': 'cond_exit',
                'sell_param': 5,
                'fee': 0.05,
                'slippage': 0.05,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['results']), 7)

        tf_codes = [r['timeframe'] for r in data['results']]
        self.assertEqual(tf_codes, ['minute15', 'minute30', 'minute60', 'minute240', 'day', 'week', 'month'])

        # Check fields of minute60 (best)
        m60 = next(r for r in data['results'] if r['timeframe'] == 'minute60')
        self.assertEqual(m60['total_trades'], 2)
        self.assertEqual(m60['win_count'], 1)
        self.assertEqual(m60['loss_count'], 1)
        self.assertEqual(m60['win_loss_display'], '1승 1패')
        self.assertEqual(m60['win_rate'], 50.0)
        self.assertEqual(m60['avg_win'], 4.0)
        self.assertEqual(m60['avg_loss'], -2.0)
        self.assertEqual(m60['payoff_ratio'], 2.0)
        self.assertEqual(m60['profit_factor'], 2.0)
        self.assertEqual(m60['total_return'], 10.0)
        self.assertEqual(m60['mdd'], 2.5)
        self.assertTrue(m60['is_best'])

    @patch('coinscreener.screener.views.research_views.run_backtest')
    def test_timeframe_comparison_handles_insufficient_data(self, mock_backtest):
        def _mock_run(ticker, conditions, candle_count, sell_mode, sell_param, fee, slippage):
            tf = conditions[0].timeframe
            if tf == 'month':
                return {'error': '데이터가 부족하여 백테스팅을 실행할 수 없습니다.'}
            return {
                'trades': [{'return_pct': 3.0, 'entry_date': '2026-01-01', 'exit_date': '2026-01-02'}],
                'total_trades': 1,
                'win_rate': 100.0,
                'total_return': 3.0,
                'mdd': 0.0,
            }
        mock_backtest.side_effect = _mock_run

        response = self.client.post(
            f'/strategy/{self.strategy.id}/backtest/timeframes/',
            data=json.dumps({'ticker': 'KRW-BTC'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        month_row = next(r for r in data['results'] if r['timeframe'] == 'month')
        self.assertEqual(month_row['status'], 'error')
        self.assertIn('데이터가 부족', month_row['error_msg'])

    def test_timeframe_comparison_no_conditions(self):
        empty_strat = Strategy.objects.create(name='빈전략')
        response = self.client.post(
            f'/strategy/{empty_strat.id}/backtest/timeframes/',
            data=json.dumps({'ticker': 'KRW-BTC'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    def test_modal_contains_timeframe_button(self):
        response = self.client.get(f'/strategy/{self.strategy.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="bt-tf-btn"')
        self.assertContains(response, 'id="bt-tf-modal-backdrop"')
        self.assertContains(response, '타임프레임별 백테스트 시뮬레이션 결과 요약')


