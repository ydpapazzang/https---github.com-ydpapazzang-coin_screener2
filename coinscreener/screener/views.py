from django.shortcuts import render, redirect, get_object_or_404
from .models import Strategy, Condition
from .engine import check_strategy
import pyupbit
import concurrent.futures
from django.utils import timezone
from django.contrib import messages
from django.core.cache import cache


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
    # POST 메서드만 허용 (GET 요청으로 삭제되는 것 방지)
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
    strategy = get_object_or_404(Strategy, id=strategy_id)
    conditions = strategy.conditions.all()
    return render(request, 'screener/strategy_detail.html', {
        'strategy': strategy,
        'conditions': conditions,
    })


def condition_add(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)

    # POST 메서드만 허용
    if request.method != 'POST':
        return redirect('strategy_detail', strategy_id=strategy_id)

    cond_type = request.POST.get('cond_type', '').upper()
    timeframe = request.POST.get('timeframe', 'day')
    operator = request.POST.get('operator', 'gte')

    # offset 파싱 및 검증
    try:
        offset = int(request.POST.get('offset', 0))
    except ValueError:
        messages.error(request, "n봉 전 값이 올바르지 않습니다.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    if offset < 0:
        messages.error(request, "n봉 전은 0 이상의 숫자여야 합니다.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    # cond_type별 처리
    if cond_type == 'MA':
        price_a_type = request.POST.get('ma_price_a_type', 'MA')

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
                messages.error(request, "이동평균가격A 기간은 1 이상이어야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            left_indicator, left_param = 'MA', ma_a_val

        if ma_b_val < 1:
            messages.error(request, "이동평균가격B 기간은 1 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)
        right_indicator, right_param = 'MA', ma_b_val

    elif cond_type == 'RSI':
        try:
            rsi_period = int(request.POST.get('rsi_period', 14))
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

        left_indicator, left_param = 'RSI', rsi_period
        right_indicator, right_param = 'VAL', rsi_threshold

    else:
        # 알 수 없는 cond_type → UnboundLocalError 방지
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
    # POST 메서드만 허용 (GET으로 삭제되는 CSRF 우회 방지)
    if request.method == 'POST':
        condition = get_object_or_404(Condition, id=condition_id)
        condition.delete()
        cache.delete(f"strategy_results_{strategy_id}")
    return redirect('strategy_detail', strategy_id=strategy_id)


# ──────────────────────────────────────────
# 3. 코인 검색 (스크리닝 실행)
# ──────────────────────────────────────────

def coin_search(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())

    # 조건 없이 실행하면 전체 코인이 히트되는 버그 방지
    if not conditions:
        messages.warning(request, "조건을 먼저 추가해주세요.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    # 캐시 확인 (?refresh=1 이면 강제 새로고침)
    cache_key = f"strategy_results_{strategy_id}"
    cached_data = cache.get(cache_key)

    if cached_data and request.GET.get('refresh') != '1':
        return render(request, 'screener/coin_list.html', {
            'results': cached_data['results'],
            'strategy': strategy,
            'rate_limit_warning': cached_data['rate_limit_warning'],
            'is_cached': True,
            'last_updated': cached_data.get('last_updated'),
        })

    tickers = pyupbit.get_tickers(fiat="KRW")
    results = []

    def process_ticker(ticker):
        try:
            is_match, details, price, volume = check_strategy(ticker, conditions)
            if price is None:
                return "API_ERROR"
            if is_match:
                unique_details = list(dict.fromkeys(details))
                return {
                    'symbol': ticker,
                    'price': price,
                    'details': ", ".join(unique_details),
                    'volume': volume,
                    'volume_display': f"{volume / 100_000_000:.1f}억",
                }
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_ticker, t): t for t in tickers}
        error_occurred = False
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result == "API_ERROR":
                error_occurred = True
            elif result:
                results.append(result)

    results.sort(key=lambda x: x.get('volume', 0), reverse=True)
    last_updated = timezone.now()

    cache.set(cache_key, {
        'results': results,
        'rate_limit_warning': error_occurred,
        'last_updated': last_updated,
    }, timeout=300)

    return render(request, 'screener/coin_list.html', {
        'results': results,
        'strategy': strategy,
        'rate_limit_warning': error_occurred,
        'last_updated': last_updated,
    })
