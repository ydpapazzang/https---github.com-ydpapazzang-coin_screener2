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

logger = logging.getLogger(__name__)

def _get_cron_secret():
    return os.environ.get('CRON_SECRET', '')



def clear_strategy_cache(strategy_id):
    exchanges = ['upbit', 'bithumb']
    vol_limits = [0, 30, 50, 80, 100, 200]
    cache.delete(f"strategy_results_{strategy_id}")
    for ex in exchanges:
        for vol in vol_limits:
            cache.delete(f"strategy_results_{strategy_id}_{ex}_{vol}")


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
            clear_strategy_cache(s_id)
        Strategy.objects.filter(id__in=strategy_ids).delete()
    return redirect('strategy_list')




def strategy_detail(request, strategy_id):
    strategies = Strategy.objects.all().order_by('-created_at')
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = strategy.conditions.all()
    # 최근 100건의 알림 이력 조회
    histories  = strategy.histories.all().order_by('-created_at')[:100]
    return render(request, 'screener/strategy_trading.html', {
        'strategies': strategies,
        'strategy':   strategy,
        'conditions': conditions,
        'histories':  histories,
    })


def condition_add(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)

    if request.method != 'POST':
        return redirect('strategy_detail', strategy_id=strategy_id)

    cond_type = request.POST.get('cond_type', '').upper()
    timeframe = request.POST.get('timeframe', 'day')
    operator  = request.POST.get('operator', 'gte')
    bb_std    = None

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

        if operator == 'btw':
            try:
                rsi_threshold_max = int(request.POST.get('rsi_threshold_max', 70))
            except ValueError:
                messages.error(request, "RSI 최대 기준값이 올바르지 않습니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            if not (0 <= rsi_threshold_max <= 100):
                messages.error(request, "RSI 최대 기준값은 0에서 100 사이여야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            if rsi_threshold >= rsi_threshold_max:
                messages.error(request, "최소값은 최대값보다 작아야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            bb_std = float(rsi_threshold_max)

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
        bb_std                       = 2.0

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

    elif cond_type == 'IC':
        ic_comparison = request.POST.get('ic_comparison', 'TENKAN_KIJUN')
        valid_ic = ('TENKAN_KIJUN', 'CLOSE_SPAN_A', 'CLOSE_SPAN_B', 'SPAN_A_SPAN_B', 'CHIKOU_CLOSE', 'CLOSE_KIJUN', 'CLOSE_CLOUD')
        if ic_comparison not in valid_ic:
            messages.error(request, "올바르지 않은 일목균형표 비교 유형입니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        if ic_comparison == 'TENKAN_KIJUN':
            left_indicator, left_param = 'IC_TENKAN', 9
            right_indicator, right_param = 'IC_KIJUN', 26
        elif ic_comparison == 'CLOSE_SPAN_A':
            left_indicator, left_param = 'CLOSE', 0
            right_indicator, right_param = 'IC_SPAN_A', 26
        elif ic_comparison == 'CLOSE_SPAN_B':
            left_indicator, left_param = 'CLOSE', 0
            right_indicator, right_param = 'IC_SPAN_B', 26
        elif ic_comparison == 'SPAN_A_SPAN_B':
            left_indicator, left_param = 'IC_SPAN_A', 26
            right_indicator, right_param = 'IC_SPAN_B', 26
        elif ic_comparison == 'CHIKOU_CLOSE':
            left_indicator, left_param = 'IC_CHIKOU', 0
            right_indicator, right_param = 'IC_CHIKOU_REF', 26
        elif ic_comparison == 'CLOSE_KIJUN':
            left_indicator, left_param = 'CLOSE', 0
            right_indicator, right_param = 'IC_KIJUN', 26
        elif ic_comparison == 'CLOSE_CLOUD':
            left_indicator, left_param = 'CLOSE', 0
            if operator in ('cross_down', 'lte', 'lt'):
                right_indicator = 'IC_CLOUD_BOTTOM'
            else:
                right_indicator = 'IC_CLOUD_TOP'
            right_param = 26

    elif cond_type == 'VOLUME':
        volume_target = request.POST.get('volume_target', 'prev')
        try:
            volume_pct = int(request.POST.get('volume_pct', 150))
        except ValueError:
            volume_pct = 150

        if volume_pct < 1:
            messages.error(request, "기준 비율은 1% 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        bb_std = volume_pct / 100.0  # multiplier
        left_indicator = 'VOLUME'
        left_param = 0

        if operator == 'btw':
            try:
                volume_pct_max = int(request.POST.get('volume_pct_max', 300))
            except ValueError:
                messages.error(request, "최대 기준 비율이 올바르지 않습니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)

            if volume_pct_max <= volume_pct:
                messages.error(request, "최대 비율은 최소 비율보다 커야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            left_param = volume_pct_max

        if volume_target == 'prev':
            right_indicator = 'VOLUME_PREV'
            right_param = 1
        elif volume_target == 'ma':
            try:
                volume_period = int(request.POST.get('volume_period', 5))
            except ValueError:
                volume_period = 5
            if volume_period < 1:
                messages.error(request, "평균 거래량 기간은 1 이상이어야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            right_indicator = 'VOLUME_MA'
            right_param = volume_period
        else:
            messages.error(request, "올바르지 않은 거래량 비교 유형입니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

    elif cond_type == 'CHANGE_RATE':
        try:
            cr_threshold = float(request.POST.get('cr_threshold', 0))
        except ValueError:
            messages.error(request, "등락률 기준값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)
        left_indicator, left_param   = 'CHANGE_RATE', 0
        right_indicator, right_param = 'VAL', round(cr_threshold)

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
        bb_std=bb_std,
    )
    clear_strategy_cache(strategy_id)
    return redirect('strategy_detail', strategy_id=strategy_id)


def condition_delete(request, strategy_id, condition_id):
    if request.method == 'POST':
        condition = get_object_or_404(Condition, id=condition_id)
        condition.delete()
        clear_strategy_cache(strategy_id)
    return redirect('strategy_detail', strategy_id=strategy_id)




# from ..models import AlertSetting
from .. import telegram as tg
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET, require_GET
from django.views.decorators.csrf import csrf_exempt
import json as json


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
                'exchange': 'upbit', 'vol_limit': 0}
    data['tg_configured'] = tg.is_configured()
    return JsonResponse(data)


@require_POST
def alert_save(request, strategy_id):
    """POST: 알림 설정 저장"""
    strategy = get_object_or_404(Strategy, id=strategy_id)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': '잘못된 요청'}, status=400)

    try:
        alert_hour = 9  # Vercel Hobby 크론 제한(하루 1회)으로 오전 9시 고정
        alert_min  = 0  # 30분 단위 제외, 정각만 사용
        vol_limit  = int(body.get('vol_limit', 0))
    except (ValueError, TypeError) as e:
        return JsonResponse({'ok': False, 'error': f'숫자 형식 오류: {e}'}, status=400)

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


def process_scan_and_alert(strategy, tickers, conditions):
    """
    주어진 전략과 티커 목록을 대상으로 스캔하고,
    알림 이력 저장 및 12시간 중복 방지 필터링을 거친 결과를 반환합니다.
    """
    from django.utils import timezone
    import datetime

    _bulk_prefetch_ohlcv(tickers, conditions)

    results = []

    def _proc(t_data):
        ticker = t_data['ticker'] if isinstance(t_data, dict) else t_data
        fallback_name = ticker.replace('KRW-', '')
        name = t_data.get('name', fallback_name) if isinstance(t_data, dict) else cache.get(f"kospi_name_{ticker}", fallback_name)
        fast_price = t_data.get('current_price') if isinstance(t_data, dict) else None
        fast_change_rate = t_data.get('change_rate') if isinstance(t_data, dict) else None

        try:
            is_match, details, price, volume, change_rate, status = check_strategy(
                ticker, conditions, 
                current_price=fast_price, 
                current_change_rate=fast_change_rate
            )
            if is_match and price:
                details_str = ", ".join(list(dict.fromkeys(details)))
                should_notify = True
                AlertHistory.objects.create(
                    strategy=strategy,
                    symbol=ticker,
                    price=price,
                    volume=volume,
                    details=details_str,
                    status=status,
                    is_notified=should_notify
                )
                return {
                    'symbol':         ticker,
                    'name':           name,
                    'price':          price,
                    'volume':         volume,
                    'volume_display': f"{volume / 100_000_000:.1f}억",
                    'status':         status,
                    'details':        details_str,
                    'should_notify':  should_notify
                }
        except Exception as e:
            print(f"Error scanning {ticker}: {e}")
        return None

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        for r in executor.map(_proc, tickers):
            if r:
                results.append(r)
                
    # 거래대금 순으로 정렬
    results.sort(key=lambda x: x.get('volume', 0), reverse=True)
    
    # 텔레그램용 결과: 중복 발송 방지 처리된(should_notify=True) 코인들만 선별
    tg_results = [r for r in results if r['should_notify']]
    
    return results, tg_results


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
        body = json.loads(request.body)
    except Exception:
        body = {}

    # body 파싱 실패해도 안전하게 기본값 사용
    exchange  = body.get('exchange', 'upbit') or 'upbit'
    try:
        vol_limit_val = body.get('vol_limit')
        vol_limit = int(vol_limit_val) if vol_limit_val is not None else 0
    except (ValueError, TypeError):
        vol_limit = 0

    tickers = _get_tickers(exchange, vol_limit)
    results, tg_results = process_scan_and_alert(strategy, tickers, conditions)

    res = tg.send_alert(strategy.name, tg_results, strategy_id=strategy.id, exchange=exchange)
    if res['ok']:
        return JsonResponse({'ok': True, 'matched': len(results), 'sent': len(tg_results)})
    return JsonResponse({'ok': False, 'error': res['error']})


def open_market(request):
    """종목 딥링크 리다이렉터.
    텔레그램 인앱 브라우저는 유니버설 링크를 앱으로 넘기지 않으므로,
    이 페이지에서 앱 커스텀 스킴(iOS)/intent(Android)를 직접 시도하고
    실패 시 모바일 웹으로 폴백한다.
    """
    from urllib.parse import quote
    ex  = request.GET.get('ex', 'upbit')
    sym = request.GET.get('sym', '')
    web_url = tg.market_link(ex, sym)
    coin = sym.replace('KRW-', '')
    fb = quote(web_url, safe='')

    # Android: 앱 커스텀 스킴을 intent로 시도한다.
    # https App Link는 앱이 등록하지 않아 실패했으므로, 커스텀 스킴(upbit:// 등) + 패키지 명시로 앱에 직접 전달.
    # 스킴 host/path는 앱마다 다르며 미검증. 실패 시 browser_fallback_url로 웹 폴백.
    market = sym if sym.startswith('KRW-') else f'KRW-{coin}'
    ANDROID_PKG = {
        'upbit':   'com.dunamu.exchange',
        'bithumb': 'com.btckorea.bithumb',
        'kospi':   'com.nhn.android.search',
    }
    # (scheme, scheme 뒤 host+path+query)
    SCHEME_TARGET = {
        'upbit':   ('upbit',   f'exchange?code=CRIX.UPBIT.{market}'),
        'bithumb': ('bithumb', f'fx/trade?coinType={coin}&crncCd=KRW'),
    }
    host_path = web_url.replace('https://', '').replace('http://', '')
    if ex in SCHEME_TARGET:
        scheme, target = SCHEME_TARGET[ex]
        pkg = ANDROID_PKG.get(ex, '')
        android_intent = (f"intent://{target}#Intent;scheme={scheme};"
                          f"package={pkg};S.browser_fallback_url={fb};end")
    else:
        # 코스피 등: https App Link + 패키지(네이버앱)
        pkg = ANDROID_PKG.get(ex, '')
        pkg_part = f"package={pkg};" if pkg else ""
        android_intent = (f"intent://{host_path}#Intent;scheme=https;"
                          f"{pkg_part}S.browser_fallback_url={fb};end")

    # Chrome 재오픈(2차 시도용): App Link 검증이 켜진 기기에서 앱으로 전환
    android_chrome = (f"intent://{host_path}#Intent;scheme=https;"
                      f"package=com.android.chrome;S.browser_fallback_url={fb};end")

    # iOS: 커스텀 스킴 best-effort (실패 시 웹 폴백)
    ios_scheme = ''
    if ex == 'upbit':
        ios_scheme = f"upbit://exchange?code=CRIX.UPBIT.{market}"
    elif ex == 'bithumb':
        ios_scheme = f"bithumb://fx/trade?coinType={coin}&crncCd=KRW"

    return render(request, 'screener/open_redirect.html', {
        'web_url': web_url,
        'android_intent': android_intent,
        'android_chrome': android_chrome,
        'ios_scheme': ios_scheme,
        'symbol': sym,
        'exchange': ex,
    })


