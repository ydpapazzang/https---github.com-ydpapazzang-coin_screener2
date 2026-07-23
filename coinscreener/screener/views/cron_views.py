import os
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
from .scan_views import _get_tickers, _bulk_prefetch_ohlcv
from .. import telegram as tg
from .strategy_views import process_scan_and_alert

logger = logging.getLogger(__name__)

def _get_cron_secret():
    return os.environ.get('CRON_SECRET', '')



from ..backtest import run_backtest, MAJOR_COINS


@require_GET
def backtest_coins(request):
    """GET: 메이저 코인 목록 반환"""
    return JsonResponse({'coins': MAJOR_COINS})


@require_POST
def backtest_run(request, strategy_id):
    """POST: 백테스팅 실행"""
    strategy   = get_object_or_404(Strategy, id=strategy_id)
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
    """Vercel Cron: 30분 주기로 한국 표준시(KST)를 계산하여, 예약된 활성 알림 스캔 및 텔레그램 발송"""
    from django.http import HttpResponseForbidden
    import traceback
    
    # 보안 검증: Vercel Cron이거나 디버그 시크릿이 있는 경우만 허용
    cron_sec = _get_cron_secret()
    auth_header = request.headers.get('Authorization', '')
    
    # 1. Vercel 공식 Authorization Bearer 헤더 (CRON_SECRET 연동)
    # 2. X-Vercel-Cron 헤더 (CRON_SECRET이 없을 때에도 작동하도록 허용)
    is_vercel_cron = (request.headers.get('x-vercel-cron') == '1') or (bool(cron_sec) and auth_header == f"Bearer {cron_sec}")
    
    is_debug = bool(cron_sec) and request.GET.get('secret') == cron_sec
    is_force = request.GET.get('force') == 'true'
    
    print(f"[CRON_SCAN] Triggered. is_vercel_cron={is_vercel_cron}, is_debug={is_debug}, is_force={is_force}")
    print(f"[CRON_SCAN] Headers: {dict(request.headers)}")
    
    if not is_vercel_cron and not is_debug:
        print("[CRON_SCAN] Security check failed: Forbidden access.")
        return HttpResponseForbidden("권한이 없습니다.")
        
    try:
        from django.utils import timezone
        import datetime
        
        # 한국 표준시(KST) 구하기 (settings.py의 TIME_ZONE='Asia/Seoul' 및 USE_TZ=True 연동)
        now_kst = timezone.localtime(timezone.now())
        print(f"[CRON_SCAN] Current KST time: {now_kst}")

        # 가장 가까운 정각(1시간 단위)으로 반올림 (30분 오차 범위 보정)
        rounded_time = now_kst + datetime.timedelta(minutes=30)
        current_hour = rounded_time.hour
        
        # 기본적으로 시간 필터를 적용하여 사용자가 설정한 시간(alert_hour)에만 스캔 수행
        # 단, 수동 강제 테스트(&force=true) 시에는 시간 필터 없이 전체 스캔
        if is_force:
            active_settings = AlertSetting.objects.filter(enabled=True)
            print(f"[CRON_SCAN] (FORCE) Scanning all {active_settings.count()} active settings ignoring time.")
        else:
            active_settings = AlertSetting.objects.filter(
                enabled=True,
                alert_hour=current_hour
            )
            print(f"[CRON_SCAN] Scanning {active_settings.count()} active settings matching KST hour {current_hour}.")
            
        processed_count = 0
        sent_count = 0
        results_summary = []
        warnings = []
        
        if not active_settings.exists():
            if is_force:
                warnings.append("활성화된 알림 설정(AlertSetting)이 존재하지 않습니다. Vercel 배포 시 SQLite 데이터가 초기화되었거나, 웹 페이지에서 알림 설정을 켜지 않았을 수 있습니다.")
            else:
                warnings.append(f"현재 KST {current_hour}시에 예약 활성화된 알림 설정이 없습니다. (만약 즉시 강제 테스트를 원하시면 URL 뒤에 &force=true 를 붙여 접속해 주세요.)")
        
        for setting in active_settings:
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
            
            results, tg_results = process_scan_and_alert(strategy, tickers, conditions)
            print(f"[CRON_SCAN] Scan results: total matched = {len(results)}, notify list = {len(tg_results)}")
            
            # 텔레그램 발송 (중복 방지 처리된 tg_results 사용)
            if tg.is_configured():
                res = tg.send_alert(strategy.name, tg_results, strategy_id=strategy.id, exchange=setting.exchange)
                print(f"[CRON_SCAN] Telegram send result: {res}")
                if res.get('ok'):
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
                warn_msg = f"전략 '{strategy.name}': 텔레그램 환경변수(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)가 Vercel에 설정되지 않았습니다."
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
    strategies = Strategy.objects.all().order_by('-created_at')
    
    if strategy_id is None:
        first_strat = strategies.first()
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
    strategy = get_object_or_404(Strategy, id=strategy_id)
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
    strategy = get_object_or_404(Strategy, id=strategy_id)
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
    strategy = get_object_or_404(Strategy, id=strategy_id)
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

    tickers = _get_tickers(exchange, vol_limit)
    _bulk_prefetch_ohlcv(tickers, conditions)
    results = []
    error_occurred = False

    def process_ticker(t_data):
        ticker = t_data['ticker'] if isinstance(t_data, dict) else t_data
        try:
            is_match, details, price, volume, change_rate, status = check_strategy(ticker, conditions)
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







