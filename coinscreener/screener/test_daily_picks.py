from datetime import date

import pandas as pd
from django.test import SimpleTestCase
from unittest.mock import patch

from .daily_picks import (
    RecommendationRejected,
    build_recommendation,
    is_stablecoin_ticker,
    optimize_breakout_k,
    rank_recommendations,
    validate_daily_candles,
)


class DailyPickGuardrailTestCase(SimpleTestCase):
    def _candles(self, today=date(2026, 8, 21)):
        index = pd.date_range(end=pd.Timestamp(today), periods=16, freq="D")
        return pd.DataFrame(
            {
                "open": [100.0] * 16,
                "high": [103.0] * 16,
                "low": [99.0] * 16,
                "close": [102.0] * 16,
                "volume": [1000.0] * 16,
                "value": [100000.0] * 16,
            },
            index=index,
        )

    def test_stablecoins_are_excluded(self):
        self.assertTrue(is_stablecoin_ticker("KRW-USDT"))
        self.assertTrue(is_stablecoin_ticker("KRW-USDC"))
        self.assertFalse(is_stablecoin_ticker("KRW-BTC"))

        with self.assertRaisesRegex(RecommendationRejected, "스테이블코인"):
            build_recommendation(
                "KRW-USDT", "USDT", None, 1400, date(2026, 8, 21)
            )

    def test_stale_daily_candle_is_rejected(self):
        candles = self._candles(today=date(2026, 8, 20))
        with self.assertRaisesRegex(RecommendationRejected, "최신 일봉 날짜"):
            validate_daily_candles(candles, date(2026, 8, 21))

    def test_invalid_ohlc_is_rejected(self):
        candles = self._candles()
        candles.iloc[-2, candles.columns.get_loc("high")] = 90.0
        with self.assertRaisesRegex(RecommendationRejected, "OHLC 관계"):
            validate_daily_candles(candles, date(2026, 8, 21))

    def test_abnormal_previous_range_is_rejected(self):
        candles = self._candles()
        candles.iloc[-2, candles.columns.get_loc("high")] = 140.0
        candles.iloc[-2, candles.columns.get_loc("low")] = 60.0

        with self.assertRaisesRegex(RecommendationRejected, "3배"):
            build_recommendation(
                "KRW-BTC", "BTC", candles, 100.0, date(2026, 8, 21)
            )

    @patch(
        "coinscreener.screener.daily_picks.optimize_breakout_k",
        return_value={
            "k_value": 0.5,
            "win_rate": 60.0,
            "trades": 5,
            "total_pct": 4.0,
        },
    )
    def test_entry_more_than_five_percent_above_market_is_rejected(
        self, _mock_optimize
    ):
        candles = self._candles()
        with self.assertRaisesRegex(RecommendationRejected, "현재가보다"):
            build_recommendation(
                "KRW-BTC", "BTC", candles, 90.0, date(2026, 8, 21)
            )

    @patch(
        "coinscreener.screener.daily_picks.optimize_breakout_k",
        return_value={
            "k_value": 0.5,
            "win_rate": 60.0,
            "trades": 5,
            "total_pct": 4.0,
        },
    )
    def test_already_crossed_entry_is_rejected(self, _mock_optimize):
        candles = self._candles()
        with self.assertRaisesRegex(RecommendationRejected, "이미 진입가"):
            build_recommendation(
                "KRW-BTC", "BTC", candles, 103.0, date(2026, 8, 21)
            )

    @patch(
        "coinscreener.screener.daily_picks.optimize_breakout_k",
        return_value={
            "k_value": 0.5,
            "win_rate": 60.0,
            "trades": 5,
            "total_pct": 4.0,
        },
    )
    def test_valid_recommendation_contains_explainable_metrics(
        self, _mock_optimize
    ):
        recommendation = build_recommendation(
            "KRW-BTC",
            "BTC",
            self._candles(),
            100.5,
            date(2026, 8, 21),
        )

        self.assertEqual(recommendation["entry_price"], 102)
        self.assertEqual(recommendation["backtest_trades"], 5)
        self.assertLess(recommendation["entry_gap_pct"], 5.0)

    def test_k_optimization_rejects_too_few_trades(self):
        index = pd.date_range(end="2026-08-21", periods=16, freq="D")
        candles = pd.DataFrame(
            {
                "open": [100.0] * 16,
                "high": [101.0] * 16,
                "low": [91.0] * 16,
                "close": [100.0] * 16,
            },
            index=index,
        )
        with self.assertRaisesRegex(RecommendationRejected, "최소 백테스트 거래"):
            optimize_breakout_k(candles)

    def test_candidates_are_ranked_by_profit_not_input_order(self):
        candidates = [
            {
                "ticker": "KRW-A",
                "backtest_profit": 2.0,
                "win_rate": 80.0,
                "backtest_trades": 3,
                "volume_value": 1000,
            },
            {
                "ticker": "KRW-B",
                "backtest_profit": 8.0,
                "win_rate": 60.0,
                "backtest_trades": 6,
                "volume_value": 500,
            },
            {
                "ticker": "KRW-C",
                "backtest_profit": 5.0,
                "win_rate": 70.0,
                "backtest_trades": 4,
                "volume_value": 700,
            },
            {
                "ticker": "KRW-D",
                "backtest_profit": 1.0,
                "win_rate": 90.0,
                "backtest_trades": 3,
                "volume_value": 2000,
            },
        ]

        ranked = rank_recommendations(candidates, limit=3)
        self.assertEqual(
            [candidate["ticker"] for candidate in ranked],
            ["KRW-B", "KRW-C", "KRW-A"],
        )
