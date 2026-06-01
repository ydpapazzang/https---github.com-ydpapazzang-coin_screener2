from django.shortcuts import render, redirect, get_object_or_404
from .models import Strategy, Condition
from .engine import check_strategy
import pyupbit
import concurrent.futures
from django.utils import timezone
from django.contrib import messages
from django.core.cache import cache
import json


# ──────────────────────────────────────────
# 1. 전략 리스트
# ──────────────────────────────────────────

def strategy_list(request):
    strategies = Strategy.objects.all().order_by('-created_at')
    return render(request, 'screener/strategy_list.html', {'strategies': strategies})


def strategy_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, "전략 이름을 입력해주세요.")
            return redirect('strategy_list')
        strategy = Strategy.objects.create(name=name)
        return redirect('strategy_detail', strategy_id=strategy.id)
    return redirect('strategy_list')


def strategy_delete(request):
    if request.method == 'POST':
        strategy_ids = request.POST.getlist('strategy_ids')
        for s_id in strategy_ids:
            cache.delete(f"strategy_results_{s_id}")
        Strategy.objects.filter(id__in=strategy_ids).delete()
    return redirect('strategy_list')


# ──────────────────────────────────────────
# 2. 전략 상세 / 조건 관리
# ──────────────────────────────────────────

def strategy_detail(request, strategy_id):
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = strategy.conditions.all()
    return render(request, 'screener/strategy_detail.html', {
        'strategy':   strategy,
        'conditions': conditions,
    })


def condition_add(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)

    if request.method != 'POST':
        return redirect('strategy_detail', strategy_id=strategy_id)

    cond_type = request.POST.get('cond_type', '').upper()
    timeframe = request.POST.get('timeframe', 'day')
    operator  = request.POST.get('operator', 'gte')

    try:
        offset = int(request.POST.get('offset', 0))
    except ValueError:
        messages.error(request, "n봉 전 값이 올바르지 않습니다.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    if offset < 0:
        messages.error(request, "n봉 전은 0 이상의 숫자여야 합니다.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    if cond_type == 'MA':
        ma_type_a    = request.POST.get('ma_type_a', 'MA')
        ma_type_b    = request.POST.get('ma_type_b', 'MA')
        price_a_type = request.POST.get('ma_price_a_type', 'MA')

        valid_ma = ('MA', 'EMA', 'WMA')
        if ma_type_a not in valid_ma: ma_type_a = 'MA'
        if ma_type_b not in valid_ma: ma_type_b = 'MA'

        try:
            ma_a_val = int(request.POST.get('ma_price_a_val', 5))
            ma_b_val = int(request.POST.get('ma_price_b_val', 20))
        except ValueError:
            messages.error(request, "이동평균 기간 값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        if price_a_type == 'CLOSE':
            left_indicator, left_param = 'CLOSE', 0
        else:
            if ma_a_val < 1:
                messages.error(request, "이동평균A 기간은 1 이상이어야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            left_indicator, left_param = ma_type_a, ma_a_val

        if ma_b_val < 1:
            messages.error(request, "이동평균B 기간은 1 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)
        right_indicator, right_param = ma_type_b, ma_b_val

    elif cond_type == 'RSI':
        try:
            rsi_period    = int(request.POST.get('rsi_period', 14))
            rsi_threshold = int(request.POST.get('rsi_threshold', 30))
        except ValueError:
            messages.error(request, "RSI 기간 또는 기준값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        if rsi_period < 1:
            messages.error(request, "RSI 기간은 1 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)
        if not (0 <= rsi_threshold <= 100):
            messages.error(request, "RSI 기준값은 0에서 100 사이여야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        left_indicator, left_param   = 'RSI', rsi_period
        right_indicator, right_param = 'VAL', rsi_threshold

    elif cond_type == 'BB':
        try:
            bb_period = int(request.POST.get('bb_period', 20))
            bb_target = request.POST.get('bb_target', 'BB_UPPER')
        except ValueError:
            messages.error(request, "볼린저밴드 기간 값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        valid_bb = ('BB_UPPER', 'BB_MIDDLE', 'BB_LOWER')
        if bb_target not in valid_bb: bb_target = 'BB_UPPER'

        if bb_period < 1:
            messages.error(request, "볼린저밴드 기간은 1 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        left_indicator, left_param   = 'CLOSE', 0
        right_indicator, right_param = bb_target, bb_period

    else:
        messages.error(request, f"알 수 없는 조건 유형입니다: {cond_type}")
        return redirect('strategy_detail', strategy_id=strategy_id)

    Condition.objects.create(
        strategy=strategy,
        timeframe=timeframe,
        offset=offset,
        left_indicator=left_indicator,
        left_param=left_param,
        operator=operator,
        right_indicator=right_indicator,
        right_param=right_param,
    )
    cache.delete(f"strategy_results_{strategy_id}")
    return redirect('strategy_detail', strategy_id=strategy_id)


def condition_delete(request, strategy_id, condition_id):
    if request.method == 'POST':
        condition = get_object_or_404(Condition, id=condition_id)
        condition.delete()
        cache.delete(f"strategy_results_{strategy_id}")
    return redirect('strategy_detail', strategy_id=strategy_id)


# ──────────────────────────────────────────
# 3. 코인 검색 — SSE 스트리밍 버전
# ──────────────────────────────────────────

def _get_tickers(exchange, vol_limit):
    """거래소·거래대금 조건에 맞는 티커 목록 반환"""
    if exchange == 'bithumb':
        import requests as req
        try:
            r = req.get('https://api.bithumb.com/public/ticker/ALL_KRW', timeout=5)
            r.raise_for_status()
            body = r.json()
            # 빗썸 응답: {"status":"0000", "data":{"BTC":{...}, "date":"..."}}
            raw = body.get('data', {})
            tickers = [f"KRW-{k}" for k in raw if k != 'date']
            if vol_limit:
                def trade_val(ticker):
                    coin = ticker.replace('KRW-', '')
                    try:
                        return float(raw.get(coin, {}).get('acc_trade_value_24H') or 0)
                    except (TypeError, ValueError):
                        return 0.0
                tickers.sort(key=trade_val, reverse=True)
                tickers = tickers[:vol_limit]
        except Exception:
            tickers = []
    else:
        # 업비트 기본
        all_tickers = pyupbit.get_tickers(fiat="KRW") or []
        if vol_limit:
            # pyupbit으로 거래대금 순 정렬
            try:
                import requests as req
                symbols = [t.replace('KRW-', '') for t in all_tickers]
                r = req.get(
                    'https://api.upbit.com/v1/ticker',
                    params={'markets': ','.join(all_tickers)},
                    timeout=5
                )
                data = r.json()
                sorted_tickers = sorted(data, key=lambda x: x.get('acc_trade_price_24h', 0), reverse=True)
                tickers = [d['market'] for d in sorted_tickers[:vol_limit]]
            except Exception:
                tickers = all_tickers[:vol_limit]
        else:
            tickers = all_tickers
    return tickers


def coin_search(request, strategy_id):
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())

    if not conditions:
        messages.warning(request, "조건을 먼저 추가해주세요.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    exchange  = request.GET.get('exchange', 'upbit')
    vol_limit = int(request.GET.get('vol_limit', 0) or 0)

    cache_key   = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}"
    cached_data = cache.get(cache_key)

    if cached_data and request.GET.get('refresh') != '1':
        return render(request, 'screener/coin_list.html', {
            'results':            cached_data['results'],
            'strategy':           strategy,
            'rate_limit_warning': cached_data['rate_limit_warning'],
            'is_cached':          True,
            'last_updated':       cached_data.get('last_updated'),
        })

    # 캐시 없음 → 로딩 페이지 (JS가 SSE로 진행)
    return render(request, 'screener/search_loading.html', {
        'strategy':  strategy,
        'exchange':  exchange,
        'vol_limit': vol_limit,
    })


def coin_search_stream(request, strategy_id):
    """SSE: 검색 진행률 + 최종 결과 스트리밍"""
    from django.http import StreamingHttpResponse

    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())
    exchange   = request.GET.get('exchange', 'upbit')
    vol_limit  = int(request.GET.get('vol_limit', 0) or 0)

    def event_stream():
        if not conditions:
            yield "data: " + json.dumps({"type": "error", "msg": "조건이 없습니다."}) + "\n\n"
            return

        tickers = _get_tickers(exchange, vol_limit)
        total   = len(tickers)
        results = []
        done    = 0
        error_occurred = False

        def process_ticker(ticker):
            try:
                is_match, details, price, volume, status = check_strategy(ticker, conditions)
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

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(process_ticker, t): t for t in tickers}
            last_sent_pct = -1
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                done  += 1
                if result == "API_ERROR":
                    error_occurred = True
                elif result:
                    results.append(result)

                pct = int(done / total * 100) if total else 100
                if pct >= last_sent_pct + 2 or done == total:
                    last_sent_pct = pct
                    # 마지막 매칭 코인 심볼 전송 (로딩 화면 ticker 표시용)
                    last_match = results[-1]['symbol'] if results else None
                    yield "data: " + json.dumps({
                        "type":    "progress",
                        "done":    done,
                        "total":   total,
                        "pct":     pct,
                        "matched": len(results),
                        "last_match": last_match,
                    }) + "\n\n"

        results.sort(key=lambda x: x.get('volume', 0), reverse=True)
        last_updated = timezone.now()
        cache_key = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}"

        cache.set(cache_key, {
            'results':            results,
            'rate_limit_warning': error_occurred,
            'last_updated':       last_updated,
        }, timeout=300)

        yield "data: " + json.dumps({
            "type":     "done",
            "redirect": f"/strategy/{strategy_id}/results/?exchange={exchange}&vol_limit={vol_limit}",
        }) + "\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def coin_search_results(request, strategy_id):
    """SSE 완료 후 결과 페이지 렌더 (캐시에서 읽음)"""
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    exchange   = request.GET.get('exchange', 'upbit')
    vol_limit  = int(request.GET.get('vol_limit', 0) or 0)
    cache_key  = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}"
    cached_data = cache.get(cache_key)

    if not cached_data:
        return redirect('coin_search', strategy_id=strategy_id)

    return render(request, 'screener/coin_list.html', {
        'results':            cached_data['results'],
        'strategy':           strategy,
        'rate_limit_warning': cached_data['rate_limit_warning'],
        'is_cached':          False,
        'last_updated':       cached_data.get('last_updated'),
    })


# ──────────────────────────────────────────
# 4. 알림 설정 API
# ──────────────────────────────────────────

from .models import AlertSetting
from . import telegram as tg
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
import json as _json


@require_GET
def alert_get(request, strategy_id):
    """GET: 전략의 알림 설정 반환"""
    strategy = get_object_or_404(Strategy, id=strategy_id)
    try:
        a = strategy.alert
        data = {
            'enabled':    a.enabled,
            'alert_hour': a.alert_hour,
            'alert_min':  a.alert_min,
            'exchange':   a.exchange,
            'vol_limit':  a.vol_limit,
        }
    except Exception:
        # RelatedObjectDoesNotExist (AlertSetting.DoesNotExist의 서브클래스)를 포함한
        # 모든 "alert 없음" 예외를 안전하게 처리
        data = {'enabled': False, 'alert_hour': 9, 'alert_min': 0,
                'exchange': 'upbit', 'vol_limit': 100}
    data['tg_configured'] = tg.is_configured()
    return JsonResponse(data)


@csrf_exempt
@require_POST
def alert_save(request, strategy_id):
    """POST: 알림 설정 저장"""
    strategy = get_object_or_404(Strategy, id=strategy_id)
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': '잘못된 요청'}, status=400)

    try:
        alert_hour = int(body.get('alert_hour', 9))
        alert_min  = int(body.get('alert_min',  0))
        vol_limit  = int(body.get('vol_limit', 100))
    except (ValueError, TypeError) as e:
        return JsonResponse({'ok': False, 'error': f'숫자 형식 오류: {e}'}, status=400)

    # 범위 검증
    if not (0 <= alert_hour <= 23):
        return JsonResponse({'ok': False, 'error': '알림 시각(시)은 0~23 사이여야 합니다.'}, status=400)
    if alert_min not in (0, 30):
        return JsonResponse({'ok': False, 'error': '알림 시각(분)은 0 또는 30이어야 합니다.'}, status=400)

    enabled   = bool(body.get('enabled', False))
    exchange  = body.get('exchange', 'upbit')
    send_test = bool(body.get('send_test', False))

    AlertSetting.objects.update_or_create(
        strategy=strategy,
        defaults={
            'enabled':    enabled,
            'alert_hour': alert_hour,
            'alert_min':  alert_min,
            'exchange':   exchange,
            'vol_limit':  vol_limit,
        }
    )

    if send_test:
        if not tg.is_configured():
            return JsonResponse({'ok': False, 'error': '텔레그램 환경변수가 설정되지 않았습니다. (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)'})
        res = tg.send_message(f"🔔 [{strategy.name}] 텔레그램 알림 연결 테스트 메시지입니다.")
        if not res['ok']:
            return JsonResponse({'ok': False, 'error': res['error']})

    return JsonResponse({'ok': True})


@csrf_exempt
@require_POST
def alert_send_now(request, strategy_id):
    """POST: 즉시 스캔 후 텔레그램 발송"""
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())

    if not conditions:
        return JsonResponse({'ok': False, 'error': '조건이 없습니다.'})
    if not tg.is_configured():
        return JsonResponse({'ok': False, 'error': '텔레그램 환경변수가 설정되지 않았습니다. (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)'})

    try:
        body = _json.loads(request.body)
    except Exception:
        body = {}

    # body 파싱 실패해도 안전하게 기본값 사용
    exchange  = body.get('exchange', 'upbit') or 'upbit'
    try:
        vol_limit = int(body.get('vol_limit') or 100)
    except (ValueError, TypeError):
        vol_limit = 100

    tickers = _get_tickers(exchange, vol_limit)
    results = []

    def _proc(ticker):
        try:
            is_match, details, price, volume, status = check_strategy(ticker, conditions)
            if is_match and price:
                return {
                    'symbol':         ticker,
                    'price':          price,
                    'volume':         volume,
                    'volume_display': f"{volume / 100_000_000:.1f}억",
                    'status':         status,
                    'details':        ", ".join(list(dict.fromkeys(details))),
                }
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        for r in ex.map(_proc, tickers):
            if r:
                results.append(r)

    results.sort(key=lambda x: x.get('volume', 0), reverse=True)
    res = tg.send_alert(strategy.name, results, strategy_id=strategy.id)
    if res['ok']:
        return JsonResponse({'ok': True, 'matched': len(results)})
    return JsonResponse({'ok': False, 'error': res['error']})


# ──────────────────────────────────────────
# 5. 백테스팅 API
# ──────────────────────────────────────────

from .backtest import run_backtest, MAJOR_COINS


@require_GET
def backtest_coins(request):
    """GET: 메이저 코인 목록 반환"""
    return JsonResponse({'coins': MAJOR_COINS})


@csrf_exempt
@require_POST
def backtest_run(request, strategy_id):
    """POST: 백테스팅 실행"""
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())

    if not conditions:
        return JsonResponse({'error': '조건이 없습니다.'}, status=400)

    try:
        body         = _json.loads(request.body)
        ticker       = body.get('ticker', 'KRW-BTC')
        candle_count = int(body.get('candle_count', 200))
        sell_mode    = body.get('sell_mode', 'cond_exit')
        sell_param   = float(body.get('sell_param', 5))
    except Exception:
        return JsonResponse({'error': '잘못된 요청'}, status=400)

    # 허용 값 검증
    allowed_tickers = [c[0] for c in MAJOR_COINS]
    if ticker not in allowed_tickers:
        return JsonResponse({'error': '허용되지 않은 티커'}, status=400)
    if candle_count not in (50, 100, 200, 500):
        candle_count = 200

    result = run_backtest(ticker, conditions, candle_count, sell_mode, sell_param)
    if 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)