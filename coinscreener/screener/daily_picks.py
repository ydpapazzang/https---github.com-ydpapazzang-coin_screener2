from datetime import timedelta
import math

import numpy as np
import pandas as pd


K_CANDIDATES = (0.3, 0.4, 0.5, 0.6, 0.7)
MIN_BACKTEST_TRADES = 3
MIN_BACKTEST_WIN_RATE = 50.0
MAX_ENTRY_GAP_PCT = 5.0
MAX_PREVIOUS_RANGE_MULTIPLIER = 3.0
STABLECOIN_BASES = frozenset({"USDT", "USDC", "DAI", "TUSD", "FDUSD"})
_REQUIRED_PRICE_COLUMNS = ("open", "high", "low", "close")


class RecommendationRejected(ValueError):
    """Raised when market data does not satisfy recommendation safeguards."""


def is_stablecoin_ticker(ticker):
    base_symbol = str(ticker).upper().rsplit("-", 1)[-1]
    return base_symbol in STABLECOIN_BASES


def validate_daily_candles(df, today_date):
    if df is None or len(df) < 16:
        raise RecommendationRejected("일봉 데이터가 16개 미만입니다.")

    missing = [column for column in _REQUIRED_PRICE_COLUMNS if column not in df.columns]
    if missing:
        raise RecommendationRejected(f"필수 일봉 컬럼이 없습니다: {', '.join(missing)}")

    candles = df.copy()
    price_columns = list(_REQUIRED_PRICE_COLUMNS)
    prices = candles.loc[:, price_columns].apply(
        pd.to_numeric, errors="coerce"
    )
    if not np.isfinite(prices.to_numpy(dtype=float)).all():
        raise RecommendationRejected("일봉 가격에 NaN 또는 무한대가 포함되어 있습니다.")
    if (prices <= 0).to_numpy().any():
        raise RecommendationRejected("일봉 가격에 0 이하 값이 포함되어 있습니다.")

    invalid_ohlc = (
        (prices["high"] < prices[["open", "close"]].max(axis=1))
        | (prices["low"] > prices[["open", "close"]].min(axis=1))
        | (prices["high"] < prices["low"])
    )
    if invalid_ohlc.any():
        raise RecommendationRejected("일봉 OHLC 관계가 올바르지 않습니다.")

    if not candles.index.is_monotonic_increasing or candles.index.has_duplicates:
        raise RecommendationRejected("일봉 시간이 정렬되지 않았거나 중복되었습니다.")

    last_date = pd.Timestamp(candles.index[-1]).date()
    previous_date = pd.Timestamp(candles.index[-2]).date()
    if last_date != today_date or previous_date != today_date - timedelta(days=1):
        raise RecommendationRejected(
            f"최신 일봉 날짜가 맞지 않습니다: {previous_date} / {last_date}"
        )

    candles.loc[:, price_columns] = prices
    return candles


def optimize_breakout_k(candles):
    best_result = None

    for k_value in K_CANDIDATES:
        wins = 0
        trades = 0
        total_pct = 0.0

        for index in range(1, len(candles) - 1):
            previous = candles.iloc[index - 1]
            current = candles.iloc[index]
            candle_range = previous["high"] - previous["low"]
            entry_price = current["open"] + candle_range * k_value

            if current["high"] < entry_price:
                continue

            trades += 1
            target_price = entry_price * 1.02
            stop_loss = entry_price * 0.985

            if current["low"] <= stop_loss:
                total_pct -= 1.5
            elif current["high"] >= target_price:
                wins += 1
                total_pct += 2.0
            else:
                close_pct = (current["close"] - entry_price) / entry_price * 100
                total_pct += close_pct
                if close_pct > 0:
                    wins += 1

        if trades < MIN_BACKTEST_TRADES:
            continue

        win_rate = wins / trades * 100
        result = {
            "k_value": k_value,
            "win_rate": win_rate,
            "trades": trades,
            "total_pct": total_pct,
        }
        score = (total_pct, win_rate, trades)
        if best_result is None or score > best_result["score"]:
            result["score"] = score
            best_result = result

    if best_result is None:
        raise RecommendationRejected(
            f"최소 백테스트 거래 수({MIN_BACKTEST_TRADES}회)를 충족하지 못했습니다."
        )
    if best_result["win_rate"] < MIN_BACKTEST_WIN_RATE:
        raise RecommendationRejected(
            f"백테스트 승률이 {MIN_BACKTEST_WIN_RATE:.0f}% 미만입니다."
        )
    if best_result["total_pct"] <= 0:
        raise RecommendationRejected("백테스트 누적 수익률이 0% 이하입니다.")

    return best_result


def build_recommendation(ticker, coin_name, df, current_price, today_date):
    if is_stablecoin_ticker(ticker):
        raise RecommendationRejected("스테이블코인은 단타 추천에서 제외합니다.")

    candles = validate_daily_candles(df, today_date)
    try:
        current_price = float(current_price)
    except (TypeError, ValueError):
        raise RecommendationRejected("현재가가 숫자가 아닙니다.")
    if not math.isfinite(current_price) or current_price <= 0:
        raise RecommendationRejected("현재가가 유효하지 않습니다.")

    completed_ranges = (
        candles.iloc[:-1]["high"] - candles.iloc[:-1]["low"]
    ).astype(float)
    previous_range = float(completed_ranges.iloc[-1])
    historical_median = float(completed_ranges.iloc[:-1].tail(13).median())

    if not math.isfinite(historical_median) or historical_median <= 0:
        raise RecommendationRejected("과거 변동폭 중앙값이 유효하지 않습니다.")
    if previous_range > historical_median * MAX_PREVIOUS_RANGE_MULTIPLIER:
        raise RecommendationRejected(
            "전일 변동폭이 최근 중앙값의 "
            f"{MAX_PREVIOUS_RANGE_MULTIPLIER:.0f}배를 초과했습니다."
        )

    backtest = optimize_breakout_k(candles)
    today_open = float(candles.iloc[-1]["open"])
    entry_price = today_open + previous_range * backtest["k_value"]
    entry_gap_pct = (entry_price - current_price) / current_price * 100

    if entry_price <= current_price:
        raise RecommendationRejected("현재가가 이미 진입가에 도달하거나 돌파했습니다.")
    if entry_gap_pct > MAX_ENTRY_GAP_PCT:
        raise RecommendationRejected(
            f"진입가가 현재가보다 {entry_gap_pct:.1f}% 높습니다."
        )

    target_price = entry_price * 1.02
    stop_loss = entry_price * 0.985
    return {
        "ticker": ticker,
        "name": coin_name,
        "entry_price": round(entry_price, 2) if entry_price < 100 else int(entry_price),
        "target_price": round(target_price, 2) if target_price < 100 else int(target_price),
        "stop_loss": round(stop_loss, 2) if stop_loss < 100 else int(stop_loss),
        "k_value": backtest["k_value"],
        "win_rate": backtest["win_rate"],
        "backtest_trades": backtest["trades"],
        "backtest_profit": backtest["total_pct"],
        "entry_gap_pct": entry_gap_pct,
    }


def rank_recommendations(recommendations, limit=3):
    return sorted(
        recommendations,
        key=lambda item: (
            item["backtest_profit"],
            item["win_rate"],
            item["backtest_trades"],
            item.get("volume_value", 0),
        ),
        reverse=True,
    )[:limit]
