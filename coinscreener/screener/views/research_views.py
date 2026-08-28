import json
import re

import pyupbit
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from ..backtest import run_backtest
from ..backtest_research import build_research_report, classify_market_regimes
from ..ownership import get_viewable_strategy


def _parameters(request):
    body = json.loads(request.body or '{}')
    ticker = body.get('ticker', 'KRW-BTC')
    candle_count = int(body.get('candle_count', 500))
    sell_mode = body.get('sell_mode', 'cond_exit')
    sell_param = float(body.get('sell_param', 5))
    fee = float(body.get('fee', 0.05))
    slippage = float(body.get('slippage', 0.05))
    if not re.match(r'^KRW-[A-Z0-9]{1,20}$', ticker):
        raise ValueError('올바르지 않은 티커입니다.')
    if candle_count not in (100, 200, 500):
        raise ValueError('분석 기간은 100·200·500봉 중 하나여야 합니다.')
    if sell_mode not in ('exit_n', 'tp_sl', 'cond_exit'):
        raise ValueError('올바르지 않은 매도 방식입니다.')
    if not 0 <= fee <= 1 or not 0 <= slippage <= 1:
        raise ValueError('비용은 각각 0~1% 범위여야 합니다.')
    if sell_mode == 'exit_n' and not 1 <= sell_param <= 100:
        raise ValueError('보유 봉 수는 1~100 범위여야 합니다.')
    if sell_mode == 'tp_sl' and not 0.1 <= sell_param <= 100:
        raise ValueError('목표·손절률은 0.1~100% 범위여야 합니다.')
    return ticker, candle_count, sell_mode, sell_param, fee, slippage


@require_GET
def backtest_lab(request, strategy_id):
    strategy = get_viewable_strategy(request, strategy_id)
    return render(request, 'screener/backtest_lab.html', {'strategy': strategy})


@require_POST
def backtest_lab_run(request, strategy_id):
    strategy = get_viewable_strategy(request, strategy_id)
    conditions = list(strategy.conditions.all())
    if not conditions:
        return JsonResponse({'error': '조건이 없습니다.'}, status=400)
    try:
        ticker, count, mode, param, fee, slippage = _parameters(request)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({'error': str(exc) or '잘못된 요청입니다.'}, status=400)

    baseline = run_backtest(ticker, conditions, count, mode, param, fee, slippage)
    if 'error' in baseline:
        return JsonResponse(baseline, status=400)

    scenarios = [
        ('낮은 비용', param, fee * 0.5, slippage * 0.5),
        ('기준 설정', param, fee, slippage),
        ('높은 비용', param, min(1, fee * 1.5), min(1, slippage * 1.5)),
    ]
    if mode != 'cond_exit':
        scenarios.extend([
            ('매도값 -20%', max(0.1, param * 0.8), fee, slippage),
            ('매도값 +20%', min(100, param * 1.2), fee, slippage),
        ])
    sensitivity = []
    for label, scenario_param, scenario_fee, scenario_slippage in scenarios:
        if label == '기준 설정':
            result = baseline
        else:
            result = run_backtest(
                ticker, conditions, count, mode, scenario_param,
                scenario_fee, scenario_slippage,
            )
        sensitivity.append({'label': label, 'result': result})

    market = pyupbit.get_ohlcv('KRW-BTC', interval='day', count=max(300, count + 100))
    report = build_research_report(
        baseline, sensitivity, classify_market_regimes(market)
    )
    report['settings'] = {
        'ticker': ticker, 'candle_count': count, 'sell_mode': mode,
        'sell_param': param, 'fee': fee, 'slippage': slippage,
    }
    return JsonResponse(report)

