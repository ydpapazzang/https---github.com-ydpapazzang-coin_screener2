import json
import logging
import traceback
import concurrent.futures
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponseForbidden, HttpResponse
from django.utils import timezone
from django.contrib import messages
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
import pyupbit

from ..models import Strategy, Condition, AlertSetting, AlertHistory, OHLCVCache
from ..engine import check_strategy
from ..ownership import get_owned_strategy, get_viewable_strategy, my_and_sample_strategies
from ..cron_auth import is_cron_request_authorized
from .scan_views import _get_tickers, _bulk_prefetch_ohlcv, _effective_scan_limit
from .. import telegram as tg
from .strategy_views import process_scan_and_alert

logger = logging.getLogger(__name__)


from ..backtest import run_backtest, MAJOR_COINS


@require_GET
def backtest_coins(request):
    """GET: 메이저 코인 목록 반환"""
    return JsonResponse({'coins': MAJOR_COINS})


@require_POST
def backtest_run(request, strategy_id):
    """POST: 백테스팅 실행 (조회 가능 전략이면 샘플도 허용 — 읽기 전용 계산)"""
    strategy   = get_viewable_strategy(request, strategy_id)
    conditions = list(strategy.conditions.all())

    if not conditions:
        return JsonResponse({'error': '조건이 없습니다.'}, status=400)

    try:
        body = json.loads(request.body)
        ticker       = body.get('ticker', 'KRW-BTC')
        candle_count = int(body.get('candle_count', 200))
        sell_mode    = body.get('sell_mode', 'cond_exit')
        sell_param   = float(body.get('sell_param', 5))
        fee_pct      = float(body.get('fee', 0.05))
        slippage_pct = float(body.get('slippage', 0.05))
    except Exception:
        return JsonResponse({'error': '잘못된 요청'}, status=400)

    # candle_count 범위 고정
    if candle_count not in (50, 100, 200, 500):
        candle_count = 200
    if sell_mode not in ('exit_n', 'tp_sl', 'cond_exit'):
        return JsonResponse({'error': '올바르지 않은 매도 조건'}, status=400)
    if not 0 <= fee_pct <= 1 or not 0 <= slippage_pct <= 1:
        return JsonResponse({'error': '수수료와 슬리피지는 0~1% 범위여야 합니다.'}, status=400)
    if sell_mode == 'exit_n' and not 1 <= sell_param <= 100:
        return JsonResponse({'error': '보유 봉 수는 1~100 범위여야 합니다.'}, status=400)
    if sell_mode == 'tp_sl' and not 0.1 <= sell_param <= 100:
        return JsonResponse({'error': '익절·손절률은 0.1~100% 범위여야 합니다.'}, status=400)

    # 티커 형식 기본 검증 (KRW-XXX 형태인지만 확인)
    import re as _re
    if not _re.match(r'^KRW-[A-Z0-9]{1,20}$', ticker):
        return JsonResponse({'error': '올바르지 않은 티커 형식'}, status=400)

    result = run_backtest(
        ticker, conditions, candle_count, sell_mode, sell_param,
        fee_pct, slippage_pct,
    )
    if 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@csrf_exempt
def cron_scan(request):
    """기존 외부 cron 호환 경로. 실제 스캔은 systemd 작업이 담당한다."""
    if not is_cron_request_authorized(request):
        print("[CRON_SCAN] Security check failed: Forbidden access.")
        return HttpResponseForbidden("권한이 없습니다.")

    # 인증된 기존 cron-job.org 호출에는 성공을 반환하되 무거운 스캔은 하지
    # 않는다. 이로써 전환 중 재시도 폭주를 막고 Gunicorn 메모리를 보호한다.
    return JsonResponse({
        'ok': True,
        'disabled': True,
        'message': '예약 스캔은 systemd 작업으로 이전되었습니다.',
    })

def strategy_trading(request, strategy_id=None):
    mine, samples = my_and_sample_strategies(request)
    strategies = list(mine) + list(samples)

    if strategy_id is None:
        first_strat = strategies[0] if strategies else None
        if first_strat:
            return redirect('strategy_detail', strategy_id=first_strat.id)
        strategy = None
        conditions = []
        histories = []
    else:
        return redirect('strategy_detail', strategy_id=strategy_id)
        
    return render(request, 'screener/strategy_trading.html', {
        'strategies': strategies,
        'strategy': strategy,
        'conditions': conditions,
        'histories': histories,
    })


@require_POST
def save_risk_settings(request, strategy_id):
    strategy = get_owned_strategy(request, strategy_id)
    try:
        body = json.loads(request.body)
        stop_loss = float(body.get('stop_loss', -8.0))
        take_profit = float(body.get('take_profit', 24.0))
        capital_pct = int(body.get('capital_pct', 20))
        
        strategy.stop_loss = stop_loss
        strategy.take_profit = take_profit
        strategy.capital_pct = capital_pct
        strategy.save()
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@require_POST
def strategy_rename(request, strategy_id):
    strategy = get_owned_strategy(request, strategy_id)
    try:
        body = json.loads(request.body)
        new_name = body.get('name', '').strip()
        if not new_name:
            return JsonResponse({'ok': False, 'error': '전략 이름을 입력해주세요.'}, status=400)
        strategy.name = new_name
        strategy.save()
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@require_GET
def strategy_scan_count(request, strategy_id):
    strategy = get_viewable_strategy(request, strategy_id)
    conditions = list(strategy.conditions.all())
    
    if not conditions:
        return JsonResponse({'ok': True, 'count': 0})
        
    exchange = request.GET.get('exchange', 'upbit')
    try:
        vol_limit_param = request.GET.get('vol_limit')
        vol_limit = int(vol_limit_param) if vol_limit_param is not None else 0
    except (ValueError, TypeError):
        vol_limit = 0
        
    tf_override = request.GET.get('timeframe')
    if tf_override:
        for c in conditions:
            c.timeframe = tf_override

    # (B) 검색 결과와 동일 범위로 카운트하도록 상위 N 캡을 함께 적용 (full=1이면 전체)
    full_scan = request.GET.get('full') == '1'
    scan_limit = _effective_scan_limit(exchange, vol_limit, full_scan)
    tickers = _get_tickers(exchange, scan_limit)
    _bulk_prefetch_ohlcv(tickers, conditions, exchange=exchange)
    results = []
    error_occurred = False

    def process_ticker(t_data):
        ticker = t_data['ticker'] if isinstance(t_data, dict) else t_data
        fast_price = t_data.get('current_price') if isinstance(t_data, dict) else None
        fast_change_rate = t_data.get('change_rate') if isinstance(t_data, dict) else None
        
        try:
            is_match, details, price, volume, change_rate, status = check_strategy(
                ticker, conditions,
                current_price=fast_price,
                current_change_rate=fast_change_rate,
                exchange=exchange,
                persist_db=False,
            )
            if price is None:
                return "API_ERROR"
            if is_match:
                unique_details = list(dict.fromkeys(details))
                return {
                    'symbol':         ticker,
                    'price':          price,
                    'details':        ", ".join(unique_details),
                    'volume':         volume,
                    'volume_display': f"{volume / 100_000_000:.1f}억",
                    'status':         status,
                }
        except Exception:
            pass
        return None

    api_error_count = 0
    none_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_ticker, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res == "API_ERROR":
                error_occurred = True
                api_error_count += 1
            elif res:
                results.append(res)
            else:
                none_count += 1

    results.sort(key=lambda x: x.get('volume', 0), reverse=True)
    last_updated = timezone.now()
    cache_key = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}"

    cache.set(cache_key, {
        'results':            results,
        'rate_limit_warning': error_occurred,
        'last_updated':       last_updated,
    }, timeout=300)

    debug = request.GET.get('debug') == '1'
    resp = {'ok': True, 'count': len(results)}
    if debug:
        from ..models import OHLCVCache
        active_timeframes = list(set(c.timeframe for c in conditions))
        db_count = OHLCVCache.objects.filter(timeframe__in=active_timeframes).count()
        resp['_debug'] = {
            'total_tickers': len(tickers),
            'api_error_count': api_error_count,
            'none_count': none_count,
            'active_timeframes': active_timeframes,
            'ohlcvcache_rows': db_count,
            'conditions': [{'tf': c.timeframe, 'left': c.left_indicator, 'op': c.operator, 'right': c.right_indicator} for c in conditions],
        }
    return JsonResponse(resp)

