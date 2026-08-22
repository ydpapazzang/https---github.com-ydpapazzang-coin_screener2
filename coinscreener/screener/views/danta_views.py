from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from ..models import DailyRecommendation


def _display_date():
    """KST 오전 9시 이전에는 직전 추천일을 표시한다."""
    now_kst = timezone.localtime()
    display_date = now_kst.date()
    if now_kst.hour < 9:
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
    recommendations = DailyRecommendation.objects.filter(
        date=display_date,
        trade_type='swing',
    )

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

    # 상단 통계는 현재 필터 결과를 기준으로 계산한다.
    total = recommendations.count()
    wins = recommendations.filter(status='success').count()
    losses = recommendations.filter(status='failed').count()
    decided = wins + losses
    win_rate = (wins / decided * 100) if decided else 0

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
        'win_rate': round(win_rate, 1),
        'query': query,
        'selected_status': status,
        'selected_trade_type': trade_type,
        'date_from': date_from_raw if date_from else '',
        'date_to': date_to_raw if date_to else '',
        'status_choices': DailyRecommendation.status_choices,
        'trade_type_choices': DailyRecommendation.trade_type_choices,
        'filter_query': query_params.urlencode(),
    })
