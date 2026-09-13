from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from ..danta_dual_timeframe import DANTA_PROFILES
from ..models import DailyRecommendation
from ..strategy_confidence import build_confidence_report


def _display_date():
    """KST 오전 9시 10분 이전에는 직전 추천일을 표시한다."""
    now_kst = timezone.localtime()
    display_date = now_kst.date()
    if (now_kst.hour, now_kst.minute) < (9, 10):
        display_date = display_date - timezone.timedelta(days=1)
    return display_date


def danta_list(request, profile_key='v1'):
    """선택한 단타 프로필(V1 또는 V2)의 오늘 추천 탭."""
    display_date = _display_date()
    profile = DANTA_PROFILES.get(profile_key, DANTA_PROFILES['v1'])
    profile_filter = Q(strategy_version=profile.strategy_version)
    # 버전 기록 전의 과거 단타는 보수적인 V1 화면에서만 조회한다.
    if profile.key == 'v1':
        profile_filter |= Q(strategy_version='')
    recommendations = list(DailyRecommendation.objects.filter(
        date=display_date,
        trade_type='danta',
    ).filter(profile_filter).order_by('-created_at'))

    # 탭의 현재가는 저장된 진입가와 구분해 가볍게 일괄 보강한다.
    active_tickers = [
        rec.coin_ticker for rec in recommendations
        if rec.coin_ticker.startswith('KRW-') and rec.status in ('active', 'partial')
    ]
    if active_tickers:
        try:
            import pyupbit
            prices = pyupbit.get_current_price(active_tickers)
            if isinstance(prices, dict):
                for rec in recommendations:
                    rec.current_price = prices.get(rec.coin_ticker)
        except Exception:
            pass

    return render(request, 'screener/danta_list.html', {
        'recommendations': recommendations,
        'profile': profile,
        'profile_first_take_profit_pct': round(
            profile.first_take_profit_fraction * 100,
        ),
        'date': display_date,
    })


def swing_list(request):
    """중지한 스윙 화면의 기존 주소는 단타 V1으로 보낸다."""
    return redirect('danta_v1')


def _parse_filter_date(raw_value):
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except (TypeError, ValueError):
        return None


def stats_list(request):
    """검색·필터·상세 조회를 제공하는 단타·스윙 추천 성적 탭."""
    recommendations = DailyRecommendation.objects.all()

    query = request.GET.get('q', '').strip()[:50]
    status = request.GET.get('status', '').strip()
    raw_trade_type = request.GET.get('trade_type')
    trade_type = raw_trade_type.strip() if raw_trade_type is not None else 'danta_all'
    strategy_version = request.GET.get('strategy_version', '').strip()[:80]
    date_from_raw = request.GET.get('date_from', '').strip()
    date_to_raw = request.GET.get('date_to', '').strip()
    date_from = _parse_filter_date(date_from_raw)
    date_to = _parse_filter_date(date_to_raw)

    valid_statuses = {value for value, _label in DailyRecommendation.status_choices}
    if query:
        recommendations = recommendations.filter(
            Q(coin_name__icontains=query) | Q(coin_ticker__icontains=query)
        )
    if status in valid_statuses:
        recommendations = recommendations.filter(status=status)
    else:
        status = ''
    # 스윙은 운영을 중지했으므로 성적표도 단타 V1·V2만 제공한다.
    danta_profiles = DANTA_PROFILES
    recommendations = recommendations.filter(trade_type='danta')
    if trade_type == 'danta_v1':
        recommendations = recommendations.filter(
            Q(strategy_version=danta_profiles['v1'].strategy_version)
            | Q(strategy_version=''),
        )
    elif trade_type == 'danta_v2':
        recommendations = recommendations.filter(
            strategy_version=danta_profiles['v2'].strategy_version,
        )
    elif trade_type != 'danta_all':
        trade_type = ''
    available_strategy_versions = list(
        DailyRecommendation.objects.filter(trade_type='danta').exclude(strategy_version='')
        .values_list('strategy_version', flat=True).distinct().order_by('strategy_version')
    )
    if strategy_version in available_strategy_versions:
        recommendations = recommendations.filter(strategy_version=strategy_version)
    else:
        strategy_version = ''
    if date_from:
        recommendations = recommendations.filter(date__gte=date_from)
    if date_to:
        recommendations = recommendations.filter(date__lte=date_to)

    recommendations = recommendations.order_by(
        '-date', 'trade_type', 'coin_ticker'
    )

    # 추천 수와 실제 진입·확정 거래를 분리한다. 승률 분모는 결과가 있는 확정 거래뿐이다.
    total = recommendations.count()
    entered = recommendations.filter(
        Q(entered_at__isnull=False)
        | Q(status__in=['active', 'partial', 'success', 'failed'])
        | Q(status='closed', result_pct__isnull=False)
    ).count()
    decided_qs = recommendations.exclude(status='skipped').filter(
        result_pct__isnull=False,
    )
    gross_results = list(decided_qs.values_list('result_pct', flat=True))
    estimated_cost = DailyRecommendation.ESTIMATED_ROUND_TRIP_COST_PCT
    net_results = [value - estimated_cost for value in gross_results]
    epsilon = 1e-9
    wins = sum(value > epsilon for value in net_results)
    losses = sum(value < -epsilon for value in net_results)
    breakeven = len(net_results) - wins - losses
    decided = len(net_results)
    win_rate = (wins / decided * 100) if decided else 0
    avg_net_return = sum(net_results) / decided if decided else 0
    cumulative_net_return = sum(net_results) if decided else 0
    open_count = recommendations.filter(status__in=['active', 'partial']).count()
    pending_count = recommendations.filter(status='pending').count()
    no_entry_count = recommendations.filter(
        status='closed', result_pct__isnull=True,
    ).count()
    skipped_count = recommendations.filter(status='skipped').count()

    paginator = Paginator(recommendations, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    query_params = request.GET.copy()
    query_params.pop('page', None)

    context = {
        'recommendations': page_obj.object_list,
        'page_obj': page_obj,
        'total': total,
        'wins': wins,
        'losses': losses,
        'breakeven': breakeven,
        'entered': entered,
        'decided': decided,
        'win_rate': round(win_rate, 1),
        'avg_net_return': round(avg_net_return, 2),
        'cumulative_net_return': round(cumulative_net_return, 2),
        'estimated_cost': estimated_cost,
        'open_count': open_count,
        'pending_count': pending_count,
        'no_entry_count': no_entry_count,
        'skipped_count': skipped_count,
        'query': query,
        'selected_status': status,
        'selected_trade_type': trade_type,
        'selected_strategy_version': strategy_version,
        'strategy_versions': [
            (version, DailyRecommendation.strategy_profile_label_for(version))
            for version in available_strategy_versions
        ],
        'date_from': date_from_raw if date_from else '',
        'date_to': date_to_raw if date_to else '',
        'status_choices': DailyRecommendation.status_choices,
        'filter_query': query_params.urlencode(),
        'confidence_reports': [
            *(build_confidence_report(
                'danta', strategy_version=profile.strategy_version,
            ) for profile in DANTA_PROFILES.values()),
        ],
    }
    return render(request, 'screener/stats_list.html', context)

