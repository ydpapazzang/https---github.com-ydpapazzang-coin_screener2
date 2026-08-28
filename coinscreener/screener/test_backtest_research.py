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

