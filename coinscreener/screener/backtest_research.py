"""백테스트 결과를 검증 관점에서 해석하는 순수 분석 도구."""
from __future__ import annotations

import math
import random
from collections import defaultdict

import pandas as pd


def summarize_trades(trades):
    returns = [float(item['return_pct']) for item in trades]
    equity = 100.0
    peak = equity
    mdd = 0.0
    for value in returns:
        equity *= 1 + value / 100.0
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak * 100 if peak else 0.0)
    wins = [value for value in returns if value > 0]
    return {
        'total_trades': len(returns),
        'win_rate': round(len(wins) / len(returns) * 100, 1) if returns else 0.0,
        'expectancy': round(sum(returns) / len(returns), 2) if returns else 0.0,
        'total_return': round(equity - 100, 2),
        'mdd': round(mdd, 2),
    }


def split_validation(trades, train_ratio=0.7):
    ordered = sorted(trades, key=lambda item: (item['entry_date'], item['exit_date']))
    if len(ordered) < 2:
        cut = len(ordered)
    else:
        cut = min(len(ordered) - 1, max(1, int(len(ordered) * train_ratio)))
    return {
        'train_ratio': int(train_ratio * 100),
        'train': summarize_trades(ordered[:cut]),
        'validation': summarize_trades(ordered[cut:]),
    }


def walk_forward_windows(trades, windows=3):
    ordered = sorted(trades, key=lambda item: (item['entry_date'], item['exit_date']))
    if len(ordered) < 4:
        return []
    initial = max(1, len(ordered) // 2)
    remaining = len(ordered) - initial
    width = max(1, math.ceil(remaining / windows))
    results = []
    for index, start in enumerate(range(initial, len(ordered), width), 1):
        test = ordered[start:start + width]
        if not test:
            continue
        results.append({
            'window': index,
            'train_trades': start,
            'start': test[0]['entry_date'],
            'end': test[-1]['exit_date'],
            **summarize_trades(test),
        })
    return results


def classify_market_regimes(frame):
    """BTC 일봉의 50일선 위치와 20일 기울기로 상승/하락/횡보를 구분한다."""
    if frame is None or frame.empty or 'close' not in frame:
        return {}
    data = frame[['close']].copy().sort_index()
    data['ma50'] = data['close'].rolling(50, min_periods=20).mean()
    data['slope20'] = data['ma50'].pct_change(20)
    regimes = {}
    for date, row in data.dropna().iterrows():
        if row['close'] > row['ma50'] and row['slope20'] > 0.02:
            regime = '상승장'
        elif row['close'] < row['ma50'] and row['slope20'] < -0.02:
            regime = '하락장'
        else:
            regime = '횡보장'
        regimes[pd.Timestamp(date).strftime('%Y-%m-%d')] = regime
    return regimes


def regime_performance(trades, regimes):
    if not regimes:
        return []
    dates = sorted(regimes)
    grouped = defaultdict(list)
    for trade in trades:
        entry = str(trade['entry_date'])[:10]
        eligible = [date for date in dates if date <= entry]
        label = regimes[eligible[-1]] if eligible else '분류불가'
        grouped[label].append(trade)
    order = ('상승장', '횡보장', '하락장', '분류불가')
    return [
        {'regime': label, **summarize_trades(grouped[label])}
        for label in order if grouped[label]
    ]


def monte_carlo(trades, simulations=500, seed=20260828):
    returns = [float(item['return_pct']) for item in trades]
    if not returns:
        return {'simulations': 0, 'p10': 0.0, 'median': 0.0, 'p90': 0.0,
                'loss_probability': 0.0, 'median_mdd': 0.0}
    rng = random.Random(seed)
    finals, drawdowns = [], []
    for _ in range(simulations):
        equity = peak = 100.0
        max_dd = 0.0
        for value in (rng.choice(returns) for _ in returns):
            equity *= 1 + value / 100.0
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)
        finals.append(equity - 100)
        drawdowns.append(max_dd)
    finals.sort()
    drawdowns.sort()
    percentile = lambda values, ratio: values[min(len(values) - 1, int((len(values) - 1) * ratio))]
    return {
        'simulations': simulations,
        'p10': round(percentile(finals, 0.1), 2),
        'median': round(percentile(finals, 0.5), 2),
        'p90': round(percentile(finals, 0.9), 2),
        'loss_probability': round(sum(value < 0 for value in finals) / len(finals) * 100, 1),
        'median_mdd': round(percentile(drawdowns, 0.5), 2),
    }


def overfit_assessment(validation, sensitivity):
    warnings = []
    train = validation['train']
    test = validation['validation']
    if train['total_trades'] + test['total_trades'] < 30:
        warnings.append('표본 거래가 30건 미만이라 결론을 내리기 어렵습니다.')
    if train['expectancy'] > 0 and test['expectancy'] <= 0:
        warnings.append('학습구간은 수익이지만 검증구간 기대수익이 0 이하입니다.')
    if train['total_return'] > 0 and test['total_return'] < train['total_return'] * -0.25:
        warnings.append('검증구간 성과가 학습구간과 반대로 크게 악화되었습니다.')
    profitable = [row for row in sensitivity if row['total_return'] > 0]
    if sensitivity and len(profitable) < math.ceil(len(sensitivity) / 2):
        warnings.append('주변 비용·매도 설정의 절반 이상에서 수익이 유지되지 않습니다.')
    return {
        'level': '높음' if len(warnings) >= 2 else '주의' if warnings else '낮음',
        'warnings': warnings or ['현재 분석 범위에서는 뚜렷한 과최적화 신호가 없습니다.'],
    }


def build_research_report(baseline, sensitivity, regimes):
    validation = split_validation(baseline.get('trades', []))
    compact_sensitivity = [
        {
            'label': row['label'],
            **summarize_trades(row['result'].get('trades', [])),
        }
        for row in sensitivity if 'error' not in row['result']
    ]
    return {
        'baseline': {key: baseline.get(key) for key in (
            'total_trades', 'win_rate', 'expectancy', 'total_return', 'mdd', 'sharpe'
        )},
        'validation': validation,
        'walk_forward': walk_forward_windows(baseline.get('trades', [])),
        'regimes': regime_performance(baseline.get('trades', []), regimes),
        'sensitivity': compact_sensitivity,
        'monte_carlo': monte_carlo(baseline.get('trades', [])),
        'overfit': overfit_assessment(validation, compact_sensitivity),
        'disclaimer': '과거 데이터 기반 모의 결과이며 미래 수익을 보장하지 않습니다.',
    }

