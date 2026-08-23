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
def cron_scan(request):
    """크론: 30분 주기로 한국 표준시(KST)를 계산하여, 예약된 활성 알림 스캔 및 텔레그램 발송"""
    from django.http import HttpResponseForbidden
    import traceback

    # URL과 액세스 로그에 자격 증명이 남지 않도록 Authorization: Bearer 헤더만 허용한다.
    # CRON_SECRET 미설정 시 무조건 차단한다. force는 인증을 우회하지 못하며 시간 필터만 제어한다.
    if not is_cron_request_authorized(request):
        print("[CRON_SCAN] Security check failed: Forbidden access.")
        return HttpResponseForbidden("권한이 없습니다.")

    is_force = request.GET.get('force') == 'true'
    print(f"[CRON_SCAN] Triggered. is_force={is_force}")
        
    try:
        from django.utils import timezone
        import datetime
        
        # 한국 표준시(KST) 구하기 (settings.py의 TIME_ZONE='Asia/Seoul' 및 USE_TZ=True 연동)
        now_kst = timezone.localtime(timezone.now())
        print(f"[CRON_SCAN] Current KST time: {now_kst}")

        # 크론이 30분 단위로 호출되므로 현재 30분 슬롯만 처리한다.
        # 과거처럼 시간을 반올림하면 08:30과 09:00이 모두 09시 예약을 실행한다.
        slot_minute = 0 if now_kst.minute < 30 else 30
        slot_start = now_kst.replace(minute=slot_minute, second=0, microsecond=0)

        # 기본적으로 현재 슬롯과 정확히 일치하는 설정만 처리한다.
        # 단, 수동 강제 테스트(&force=true)는 시간 필터와 예약 실행 잠금을 우회한다.
        if is_force:
            active_settings = AlertSetting.objects.filter(enabled=True)
            print(f"[CRON_SCAN] (FORCE) Scanning all {active_settings.count()} active settings ignoring time.")
        else:
            active_settings = AlertSetting.objects.filter(
                enabled=True,
                alert_hour=slot_start.hour,
                alert_min=slot_start.minute,
            )
            print(
                f"[CRON_SCAN] Scanning {active_settings.count()} active settings "
                f"matching KST slot {slot_start:%H:%M}."
            )
            
        processed_count = 0
        sent_count = 0
        results_summary = []
        warnings = []
        
        if not active_settings.exists():
            if is_force:
                warnings.append("활성화된 알림 설정(AlertSetting)이 존재하지 않습니다. 웹 페이지에서 알림 설정을 켜지 않았을 수 있습니다.")
            else:
                warnings.append(f"현재 KST {slot_start:%H:%M} 슬롯에 예약 활성화된 알림 설정이 없습니다. (즉시 강제 테스트는 &force=true)")
        
        for setting in active_settings:
            if not is_force:
                # 같은 예약 슬롯이 재호출되더라도 최초 요청 하나만 원자적으로 선점한다.
                # 실패 재시도는 force=true로 명시적으로 수행할 수 있다.
                from django.db.models import Q
                claimed = AlertSetting.objects.filter(pk=setting.pk).filter(
                    Q(last_run_at__isnull=True) | Q(last_run_at__lt=slot_start)
                ).update(last_run_at=now_kst)
                if not claimed:
                    print(f"[CRON_SCAN] Already processed this slot: setting={setting.pk}")
                    continue

            strategy = setting.strategy
            print(f"[CRON_SCAN] Scanning strategy: {strategy.name} (ID: {strategy.id})")
            conditions = list(strategy.conditions.all())
            print(f"[CRON_SCAN] Strategy conditions count: {len(conditions)}")
            if not conditions:
                warn_msg = f"전략 '{strategy.name}'(ID: {strategy.id})에 조건이 존재하지 않아 스킵합니다."
                print(f"[CRON_SCAN] {warn_msg}")
                warnings.append(warn_msg)
                continue
                
            processed_count += 1
            
            # 티커 수집 (설정된 vol_limit 사용, 0인 경우 전체 코인)
            # 스캔은 OHLCVCache(사전 캐시) 기반이라 실시간 API 호출이 없어 전체 스캔도 빠름.
            # 과거 30개 강제 제한은 온라인 검색(전체)과 결과가 달라지는 원인이었으므로 제거하고
            # 사용자가 설정한 vol_limit을 그대로 사용해 온라인 결과와 일치시킴.
            vol_limit = setting.vol_limit

            tickers = _get_tickers(setting.exchange, vol_limit)
            print(f"[CRON_SCAN] Tickers count for {setting.exchange} (limit {vol_limit}): {len(tickers)}")
            
            results, tg_results = process_scan_and_alert(strategy, tickers, conditions, exchange=setting.exchange)
            print(f"[CRON_SCAN] Scan results: total matched = {len(results)}, notify list = {len(tg_results)}")
            
            # 텔레그램 발송 (중복 방지 처리된 tg_results 사용)
            if tg.is_configured():
                # 매칭은 있지만 모두 12시간 중복 억제 대상이면 '매칭 없음'이라는
                # 잘못된 메시지를 보내지 않고 조용히 건너뛴다.
                if results and not tg_results:
                    res = {'ok': True, 'skipped': True, 'reason': 'duplicate_suppressed'}
                else:
                    res = tg.send_alert(strategy.name, tg_results, strategy_id=strategy.id, exchange=setting.exchange)
                print(f"[CRON_SCAN] Telegram send result: {res}")
                if res.get('ok') and not res.get('skipped'):
                    sent_count += 1
                else:
                    warnings.append(f"텔레그램 발송 실패 ({strategy.name}): {res.get('error')}")
                results_summary.append({
                    'strategy': strategy.name,
                    'matched_count': len(results),
                    'sent_count': len(tg_results),
                    'telegram_result': res
                })
            else:
                warn_msg = f"전략 '{strategy.name}': 텔레그램 환경변수(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)가 환경변수에 설정되지 않았습니다."
                print(f"[CRON_SCAN] {warn_msg}")
                warnings.append(warn_msg)
                results_summary.append({
                    'strategy': strategy.name,
                    'matched_count': len(results),
                    'sent_count': len(tg_results),
                    'telegram_result': {'ok': False, 'error': '환경변수 미설정'}
                })
                
        return JsonResponse({
            'ok': True,
            'time': now_kst.strftime('%Y-%m-%d %H:%M:%S KST'),
            'processed': processed_count,
            'sent_alerts': sent_count,
            'warnings': warnings,
            'details': results_summary,
        })
        
    except Exception as e:
        err_msg = traceback.format_exc()
        print(f"[CRON_SCAN] Error occurred:\n{err_msg}")
        return JsonResponse({'error': f'크론 수행 중 서버 오류: {str(e)}', 'traceback': err_msg}, status=500)


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








@csrf_exempt
def cron_daily_picks(request):
    try:
        if not is_cron_request_authorized(request):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('권한이 없습니다.')
        from django.core.management import call_command
        import threading
        def run_command(): call_command('generate_daily_picks')
        t = threading.Thread(target=run_command)
        t.start()
        from django.http import JsonResponse
        return JsonResponse({'ok': True, 'message': 'Started.'})
    except Exception as e:
        from django.http import JsonResponse
        return JsonResponse({'ok': False, 'error': str(e)})

@csrf_exempt
@require_GET
def cron_swing_picks(request):
    """인증된 외부 스케줄러에서 일일 스윙 추천 생성을 시작한다."""
    try:
        if not is_cron_request_authorized(request):
            return HttpResponseForbidden('권한이 없습니다.')

        from django.core.management import call_command
        import threading

        thread = threading.Thread(
            target=lambda: call_command('generate_swing_picks'),
            name='generate-swing-picks',
        )
        thread.start()
        return JsonResponse({
            'ok': True,
            'message': 'Swing picks generation started.',
        })
    except Exception as exc:
        logger.exception("Failed to start Swing picks generation")
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)

