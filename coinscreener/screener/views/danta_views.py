from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from ..models import DailyRecommendation
from ..strategy_confidence import build_confidence_report


def _display_date():
    """KST 오전 9시 10분 이전에는 직전 추천일을 표시한다."""
    now_kst = timezone.localtime()
    display_date = now_kst.date()
    if (now_kst.hour, now_kst.minute) < (9, 10):
        display_date = display_date - timezone.timedelta(days=1)
    return display_date


def danta_list(request):
    """오늘의 단타 추천 탭."""
    display_date = _display_date()
    recommendations = DailyRecommendation.objects.filter(
        date=display_date,
        trade_type='danta',
    )

    return render(request, 'screener/danta_list.html', {
        'recommendations': recommendations,
        'date': display_date,
    })


def swing_list(request):
    """스윙 전략 확정을 위한 빈 화면과 향후 저장 데이터의 표시 기반."""
    display_date = _display_date()
    recent_cutoff = display_date - timezone.timedelta(days=30)
    recommendations = DailyRecommendation.objects.filter(
        Q(status__in=['pending', 'active', 'partial'])
        | Q(date__gte=recent_cutoff),
        trade_type='swing',
    ).order_by('-date', 'coin_ticker')

    return render(request, 'screener/swing_list.html', {
        'recommendations': recommendations,
        'date': display_date,
    })


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
    trade_type = request.GET.get('trade_type', '').strip()
    date_from_raw = request.GET.get('date_from', '').strip()
    date_to_raw = request.GET.get('date_to', '').strip()
    date_from = _parse_filter_date(date_from_raw)
    date_to = _parse_filter_date(date_to_raw)

    valid_statuses = {value for value, _label in DailyRecommendation.status_choices}
    valid_trade_types = {
        value for value, _label in DailyRecommendation.trade_type_choices
    }
    if query:
        recommendations = recommendations.filter(
            Q(coin_name__icontains=query) | Q(coin_ticker__icontains=query)
        )
    if status in valid_statuses:
        recommendations = recommendations.filter(status=status)
    else:
        status = ''
    if trade_type in valid_trade_types:
        recommendations = recommendations.filter(trade_type=trade_type)
    else:
        trade_type = ''
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

    return render(request, 'screener/stats_list.html', {
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
        'date_from': date_from_raw if date_from else '',
        'date_to': date_to_raw if date_to else '',
        'status_choices': DailyRecommendation.status_choices,
        'trade_type_choices': DailyRecommendation.trade_type_choices,
        'filter_query': query_params.urlencode(),
        'confidence_reports': [
            build_confidence_report('danta'),
            build_confidence_report('swing'),
        ],
    })

