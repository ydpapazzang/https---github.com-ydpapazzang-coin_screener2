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

    elif cond_type == 'HA':
        ha_pattern = request.POST.get('ha_pattern', 'HA_BULL')
        valid_ha = ('HA_BULL', 'HA_BEAR', 'HA_BULL_N', 'HA_BEAR_N', 'HA_NO_LOWER', 'HA_NO_UPPER')
        if ha_pattern not in valid_ha:
            messages.error(request, "올바르지 않은 하이킨아시 패턴입니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        try:
            ha_n = int(request.POST.get('ha_n', 3))
        except ValueError:
            ha_n = 3
        ha_n = max(1, min(ha_n, 20))

        # 하이킨아시는 left_indicator에 패턴, operator='is', right는 더미
        left_indicator,  left_param   = ha_pattern, ha_n
        operator                      = 'is'
        right_indicator, right_param  = 'VAL', 0

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
    # Vercel 10초 타임아웃 및 업비트 API 429 방어를 위해 최대 스캔 대상을 80개로 자동 캡핑(Capping)합니다.
    vol_limit = int(request.GET.get('vol_limit', 0) or 0)
    if vol_limit == 0 or vol_limit > 80:
        vol_limit = 80

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
    send_telegram = request.GET.get('send_telegram', '0')
    return render(request, 'screener/search_loading.html', {
        'strategy':  strategy,
        'exchange':  exchange,
        'vol_limit': vol_limit,
        'send_telegram': send_telegram,
    })


def coin_search_stream(request, strategy_id):
    """SSE: 검색 진행률 + 최종 결과 스트리밍"""
    from django.http import StreamingHttpResponse

    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())
    exchange   = request.GET.get('exchange', 'upbit')
    vol_limit  = int(request.GET.get('vol_limit', 0) or 0)
    send_telegram = request.GET.get('send_telegram') == '1'
    if vol_limit == 0 or vol_limit > 80:
        vol_limit = 80

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

        # 스레드 개수를 10개로 조절하여 업비트 API 호출의 순간 폭주(Burst)를 완화하고 Rate Limit를 방어합니다.
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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

        # 만약 자동 반복 스캔에서 텔레그램 전송이 활성화되었고, 조회된 건이 있으면 즉시 발송
        if send_telegram and results and tg.is_configured():
            try:
                tg.send_alert(strategy.name, results, strategy_id=strategy.id)
            except Exception as e:
                print(f"자동 반복 스캔 중 텔레그램 발송 실패: {e}")

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
        vol_limit = int(body.get('vol_limit') or 80)
    except (ValueError, TypeError):
        vol_limit = 80

    if vol_limit == 0 or vol_limit > 80:
        vol_limit = 80

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
        fee_pct      = float(body.get('fee', 0.05))
    except Exception:
        return JsonResponse({'error': '잘못된 요청'}, status=400)

    # candle_count 범위 고정
    if candle_count not in (50, 100, 200, 500):
        candle_count = 200

    # 티커 형식 기본 검증 (KRW-XXX 형태인지만 확인)
    import re as _re
    if not _re.match(r'^KRW-[A-Z0-9]{1,20}$', ticker):
        return JsonResponse({'error': '올바르지 않은 티커 형식'}, status=400)

    result = run_backtest(ticker, conditions, candle_count, sell_mode, sell_param, fee_pct)
    if 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@csrf_exempt
@require_POST
def ai_ask(request):
    import json
    import requests
    import os
    
    prompt = ""
    try:
        if request.content_type == 'application/json':
            body = json.loads(request.body)
            prompt = body.get('prompt', '')
        else:
            prompt = request.POST.get('prompt', '')
    except Exception:
        return JsonResponse({'error': '잘못된 요청 형식입니다.'}, status=400)
        
    if not prompt.strip():
        return JsonResponse({'error': '질문을 입력해 주세요.'}, status=400)
        
    api_key = os.environ.get('GROQ_API_KEY', '').strip()
    
    if not api_key:
        fallback_msg = (
            "⚠️ **Groq API 키가 로컬 .env 또는 Vercel 환경 변수에 설정되어 있지 않습니다.**\n\n"
            "**[설정 가이드]**\n"
            "1. 프로젝트 루트 폴더의 `.env` 파일을 열어주세요.\n"
            "2. `GROQ_API_KEY=\"발급받은키\"` 형태로 키를 입력하고 저장해 주세요.\n"
            "3. Vercel 배포 시에는 Vercel 대시보드 Settings -> Environment Variables에 `GROQ_API_KEY`를 등록하시면 정상 작동합니다.\n\n"
            "**[트레이딩 추천 전략 맛보기]**\n"
            "임시로 예시 답변을 안내해 드립니다:\n"
            "* **골든크로스 전략**: 5일 이동평균선(MA5)이 20일 이동평균선(MA20)을 상향 돌파할 때 강력한 매수 신호가 발생합니다. 본 코인 스크리너에서 캔들 단위를 '일봉'으로 설정하고 조건 'MA(5) >= MA(20)'을 추가하여 필터링해 보세요!"
        )
        return JsonResponse({'response': fallback_msg})

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {
                    "role": "system", 
                    "content": (
                        "당신은 코인 스크리너 및 트레이딩 전략 전문가 'wonii AI 비서'입니다. "
                        "사용자의 질문에 친절하고 전문적으로 답해 주세요. "
                        "답변은 가독성이 좋게 마크다운(Markdown) 서식과 이모티콘을 활용해 서술해 주세요.\n\n"
                        "★ [중요 규칙: 실시간 전략 생성 지원] ★\n"
                        "사용자가 전략 추천, 전략 생성, 단타 전략 기법, 혹은 특정 기술적 지표 활용법을 물어볼 때(예: '단타 전략 만들어줘', '단타 전략 추천', '골든크로스 전략 알려줘', 'RSI 30 이하 조건 추가' 등)에는, 상세한 텍스트 설명에 이어 **답변의 맨 마지막 줄에 사용자가 클릭 한 번으로 실제 전략과 검색조건들을 데이터베이스에 즉시 생성하고 연동할 수 있는 순수한 구조화 JSON 블록**을 무조건! 반드시! 포함해야 합니다. 단순 일반적인 안부 인사 등을 제외하고는 전략 관련 대화 시 100%의 확률로 JSON을 포함시키세요.\n\n"
                        "JSON 데이터 작성 규칙:\n"
                        "1. 주석(예: // 또는 #)을 JSON 본문에 절대로 포함하지 마십시오. 순수한 표준 JSON 규격이어야 자바스크립트의 JSON.parse가 에러 없이 작동합니다.\n"
                        "2. 사용 가능한 timeframe: 'minute1', 'minute3', 'minute5', 'minute10', 'minute15', 'minute30', 'minute60', 'minute240', 'day', 'week', 'month'. 단타(스캘핑) 전략 요청 시 5분봉('minute5') 이나 15분봉('minute15')을 활용하십시오.\n"
                        "3. 사용 가능한 지표(indicator): 'MA', 'EMA', 'WMA', 'RSI', 'BB_UPPER', 'BB_MIDDLE', 'BB_LOWER', 'HA_BULL', 'HA_BEAR', 'VAL', 'CLOSE'.\n"
                        "4. 사용 가능한 연산자(operator): 'gt', 'lt', 'gte', 'lte', 'is'.\n"
                        "5. 볼린저 밴드(BB) 조건식 설정 시: left_indicator='CLOSE', left_param=0, operator='gt'/'lt', right_indicator='BB_UPPER'/'BB_LOWER' 형태로 작성하세요 (예: 종가가 볼린저 밴드 상단을 돌파하는 조건).\n"
                        "6. 고정값 비교(예: RSI 30 이하) 설정 시: left_indicator='RSI', left_param=14, operator='lte', right_indicator='VAL', right_param=30 형태로 작성하세요.\n\n"
                        "JSON 데이터 형식 예시:\n"
                        "{\n"
                        "  \"create_strategy\": {\n"
                        "    \"name\": \"단타 EMA 크로스 전략\",\n"
                        "    \"conditions\": [\n"
                        "      {\n"
                        "        \"timeframe\": \"minute15\",\n"
                        "        \"offset\": 0,\n"
                        "        \"left_indicator\": \"CLOSE\",\n"
                        "        \"left_param\": 0,\n"
                        "        \"operator\": \"gte\",\n"
                        "        \"right_indicator\": \"EMA\",\n"
                        "        \"right_param\": 20\n"
                        "      },\n"
                        "      {\n"
                        "        \"timeframe\": \"minute15\",\n"
                        "        \"offset\": 0,\n"
                        "        \"left_indicator\": \"RSI\",\n"
                        "        \"left_param\": 14,\n"
                        "        \"operator\": \"lte\",\n"
                        "        \"right_indicator\": \"VAL\",\n"
                        "        \"right_param\": 30\n"
                        "      }\n"
                        "    ]\n"
                        "  }\n"
                        "}"
                    )
                },
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 1024
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        if response.status_code == 200:
            res_data = response.json()
            ai_response = res_data['choices'][0]['message']['content']
            return JsonResponse({'response': ai_response})
        else:
            err_msg = f"Groq API 오류 (상태 코드: {response.status_code}): {response.text}"
            print(err_msg)
            return JsonResponse({
                'response': f"⚠️ **Groq AI 호출 중 서버 오류가 발생했습니다.**\n\n디버그 메시지: `{response.text[:200]}`"
            })
            
    except requests.exceptions.Timeout:
        return JsonResponse({
            'response': "⚠️ **Groq AI API 호출 시간이 초과되었습니다 (Timeout).** 다시 시도해 주세요."
        })
    except Exception as e:
        return JsonResponse({
            'response': f"⚠️ **Groq AI 호출 중 알 수 없는 예외가 발생했습니다.**\n\n오류 내용: `{str(e)}`"
        })


@csrf_exempt
def ai_strategy_create(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST 요청만 가능합니다.'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        strategy_data = data.get('create_strategy')
        if not strategy_data:
            return JsonResponse({'error': '유효한 전략 생성 데이터가 없습니다.'}, status=400)
        
        name = strategy_data.get('name', '').strip()
        if not name:
            name = "AI 추천 전략"
            
        # Create Strategy
        strategy = Strategy.objects.create(name=name)
        
        # Create Conditions
        conditions_data = strategy_data.get('conditions', [])
        valid_timeframes = ['minute1', 'minute3', 'minute5', 'minute10', 'minute15', 'minute30', 'minute60', 'minute240', 'day', 'week', 'month']
        valid_indicators = ['MA', 'EMA', 'WMA', 'RSI', 'BB_UPPER', 'BB_MIDDLE', 'BB_LOWER', 'HA_BULL', 'HA_BEAR', 'HA_BULL_N', 'HA_BEAR_N', 'HA_NO_LOWER', 'HA_NO_UPPER', 'VAL', 'CLOSE']
        valid_operators = ['gt', 'lt', 'gte', 'lte', 'is']
        
        for c in conditions_data:
            timeframe = c.get('timeframe', 'day')
            if timeframe not in valid_timeframes:
                timeframe = 'day'
                
            try:
                offset = int(c.get('offset', 0))
            except (ValueError, TypeError):
                offset = 0
            if offset < 0:
                offset = 0
                
            left_indicator = c.get('left_indicator', 'MA')
            if left_indicator not in valid_indicators:
                left_indicator = 'MA'
                
            try:
                left_param = int(c.get('left_param', 5))
            except (ValueError, TypeError):
                left_param = 5
                
            operator = c.get('operator', 'gte')
            if operator not in valid_operators:
                operator = 'gte'
                
            right_indicator = c.get('right_indicator', 'MA')
            if right_indicator not in valid_indicators:
                right_indicator = 'MA'
                
            try:
                right_param = int(c.get('right_param', 20))
            except (ValueError, TypeError):
                right_param = 20
                
            bb_std = c.get('bb_std')
            if bb_std is not None:
                try:
                    bb_std = float(bb_std)
                except (ValueError, TypeError):
                    bb_std = None
            
            Condition.objects.create(
                strategy=strategy,
                timeframe=timeframe,
                offset=offset,
                left_indicator=left_indicator,
                left_param=left_param,
                operator=operator,
                right_indicator=right_indicator,
                right_param=right_param,
                bb_std=bb_std
            )
            
        cache.delete(f"strategy_results_{strategy.id}")
        
        # Return success with the URL to redirect to
        redirect_url = f"/strategy/{strategy.id}/"
        return JsonResponse({
            'ok': True,
            'strategy_id': strategy.id,
            'redirect_url': redirect_url
        })
        
    except Exception as e:
        return JsonResponse({'error': f'서버 내부 오류: {str(e)}'}, status=500)


