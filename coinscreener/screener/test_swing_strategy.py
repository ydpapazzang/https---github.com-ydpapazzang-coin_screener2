from datetime import date, timedelta

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from .daily_picks import RecommendationRejected
from .swing_strategy import (
    build_swing_recommendation,
    rank_swing_recommendations,
    validate_btc_regime,
)


class SwingStrategyTestCase(SimpleTestCase):
    today = date(2026, 8, 23)

    def _candles(self, falling=False):
        index = pd.date_range(end=pd.Timestamp(self.today), periods=181, freq='D')
        if falling:
            close = np.linspace(200.0, 100.0, 181)
        else:
            close = np.linspace(100.0, 200.0, 181)
        return pd.DataFrame(
            {
                'open': close - 0.5,
                'high': close + 1.0,
                'low': close - 1.5,
                'close': close,
                'volume': np.full(181, 1000.0),
                'value': np.full(181, 100_000_000.0),
            },
            index=index,
        )

    def test_btc_regime_accepts_established_uptrend(self):
        metrics = validate_btc_regime(self._candles(), self.today)

        self.assertGreater(metrics['close'], metrics['ema60'])
        self.assertGreater(metrics['ema20'], metrics['ema60'])

    def test_btc_regime_rejects_downtrend(self):
        with self.assertRaisesRegex(RecommendationRejected, '상승 조건'):
            validate_btc_regime(self._candles(falling=True), self.today)

    def test_valid_pick_uses_two_r_target_and_two_day_expiry(self):
        recommendation = build_swing_recommendation(
            'KRW-BTC',
            'BTC',
            self._candles(),
            current_price=200.0,
            today_date=self.today,
        )

        risk = recommendation['entry_price'] - recommendation['stop_loss']
        self.assertAlmostEqual(
            recommendation['target_price'],
            recommendation['entry_price'] + 2 * risk,
            delta=2,
        )
        self.assertEqual(
            recommendation['entry_expires_on'],
            self.today + timedelta(days=2),
        )
        self.assertGreaterEqual(recommendation['stop_distance_pct'], 2.0)
        self.assertLessEqual(recommendation['stop_distance_pct'], 8.0)

    def test_pick_rejects_entry_more_than_two_percent_above_market(self):
        with self.assertRaisesRegex(RecommendationRejected, '현재가보다'):
            build_swing_recommendation(
                'KRW-BTC',
                'BTC',
                self._candles(),
                current_price=190.0,
                today_date=self.today,
            )

    def test_pick_rejects_chasing_after_breakout(self):
        with self.assertRaisesRegex(RecommendationRejected, '추격 진입'):
            build_swing_recommendation(
                'KRW-BTC',
                'BTC',
                self._candles(),
                current_price=205.0,
                today_date=self.today,
            )

    def test_stablecoin_is_rejected(self):
        with self.assertRaisesRegex(RecommendationRejected, '스테이블코인'):
            build_swing_recommendation(
                'KRW-USDT',
                'USDT',
                self._candles(),
                current_price=200.0,
                today_date=self.today,
            )

    def test_ranking_prefers_momentum_then_trend_strength(self):
        candidates = [
            {
                'ticker': 'KRW-A',
                'momentum20_pct': 10.0,
                'trend_strength_pct': 12.0,
                'median_value_20': 300,
            },
            {
                'ticker': 'KRW-B',
                'momentum20_pct': 15.0,
                'trend_strength_pct': 8.0,
                'median_value_20': 100,
            },
            {
                'ticker': 'KRW-C',
                'momentum20_pct': 10.0,
                'trend_strength_pct': 15.0,
                'median_value_20': 200,
            },
        ]

        ranked = rank_swing_recommendations(candidates, limit=3)

        self.assertEqual(
            [candidate['ticker'] for candidate in ranked],
            ['KRW-B', 'KRW-C', 'KRW-A'],
        )
