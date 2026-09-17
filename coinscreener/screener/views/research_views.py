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


TARGET_TIMEFRAMES = [
    ('minute15',  '15분봉'),
    ('minute30',  '30분봉'),
    ('minute60',  '1시간봉'),
    ('minute240', '4시간봉'),
    ('day',       '일봉'),
    ('week',      '주봉'),
    ('month',     '월봉'),
]


@require_POST
def backtest_timeframe_comparison(request, strategy_id):
    """현재 전략의 조건을 15분봉~월봉(7개 타임프레임)에 적용하여 백테스트 성과를 비교."""
    import copy
    import concurrent.futures

    strategy = get_viewable_strategy(request, strategy_id)
    conditions = list(strategy.conditions.all())
    if not conditions:
        return JsonResponse({'ok': False, 'error': '전략에 등록된 조건이 없습니다.'}, status=400)

    try:
        body = json.loads(request.body or '{}')
        ticker = body.get('ticker', 'KRW-BTC')
        candle_count = int(body.get('candle_count', 200))
        sell_mode = body.get('sell_mode', 'cond_exit')
        sell_param = float(body.get('sell_param', 5))
        fee = float(body.get('fee', 0.05))
        slippage = float(body.get('slippage', 0.05))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': '잘못된 요청 파라미터입니다.'}, status=400)

    if not re.match(r'^KRW-[A-Z0-9]{1,20}$', ticker):
        return JsonResponse({'ok': False, 'error': '올바르지 않은 티커입니다.'}, status=400)
    if candle_count not in (50, 100, 200, 500):
        candle_count = 200
    if sell_mode not in ('exit_n', 'tp_sl', 'cond_exit'):
        return JsonResponse({'ok': False, 'error': '올바르지 않은 매도 방식입니다.'}, status=400)
    if not 0 <= fee <= 1 or not 0 <= slippage <= 1:
        return JsonResponse({'ok': False, 'error': '수수료와 슬리피지는 0~1% 범위여야 합니다.'}, status=400)

    def _eval_tf(tf_tuple):
        tf_code, tf_label = tf_tuple
        # 조건들의 타임프레임을 대상 타임프레임으로 복제 적용
        tf_conditions = []
        for c in conditions:
            c_copy = copy.copy(c)
            c_copy.timeframe = tf_code
            tf_conditions.append(c_copy)

        res = run_backtest(
            ticker, tf_conditions, candle_count, sell_mode, sell_param, fee, slippage
        )

        if 'error' in res:
            return {
                'timeframe': tf_code,
                'timeframe_name': tf_label,
                'display_name': f"{tf_label} ({tf_code})",
                'total_trades': 0,
                'win_count': 0,
                'loss_count': 0,
                'win_loss_display': '- / -',
                'win_rate': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'payoff_ratio': 0.0,
                'total_return': 0.0,
                'mdd': 0.0,
                'profit_factor': 0.0,
                'status': 'error',
                'error_msg': res['error'],
            }

        trades = res.get('trades', [])
        rets = [float(t['return_pct']) for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r < 0]
        win_count = len(wins)
        loss_count = len(losses)
        total_trades = len(trades)

        win_rate = round(win_count / total_trades * 100, 1) if total_trades else 0.0
        avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
        avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        payoff_ratio = round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else (99.0 if avg_win > 0 else 0.0)
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        return {
            'timeframe': tf_code,
            'timeframe_name': tf_label,
            'display_name': f"{tf_label} ({tf_code})",
            'total_trades': total_trades,
            'win_count': win_count,
            'loss_count': loss_count,
            'win_loss_display': f"{win_count}승 {loss_count}패",
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'payoff_ratio': payoff_ratio,
            'total_return': res.get('total_return', 0.0),
            'mdd': res.get('mdd', 0.0),
            'profit_factor': profit_factor,
            'status': 'ok',
        }

    # 5개 워커로 병렬 평가 (Upbit API 레이트 제한 보호 및 신속 응답)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_eval_tf, TARGET_TIMEFRAMES))

    # 최고 성과 타임프레임 선정 (총 거래수가 1회 이상이고 누적 수익률이 가장 높은 행)
    best_idx = None
    best_score = -999999.0
    for idx, r in enumerate(results):
        if r['status'] == 'ok' and r['total_trades'] > 0:
            score = r['total_return']
            if score > best_score:
                best_score = score
                best_idx = idx

    for idx, r in enumerate(results):
        r['is_best'] = (idx == best_idx and best_score > -999999.0)

    return JsonResponse({
        'ok': True,
        'strategy_name': strategy.name,
        'ticker': ticker,
        'candle_count': candle_count,
        'sell_mode': sell_mode,
        'results': results,
    })


