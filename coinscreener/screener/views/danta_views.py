from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from ..models import DailyRecommendation


def danta_list(request):
    """오늘의 단타 추천 탭"""
    now_kst = timezone.localtime()
    today_date = now_kst.date()

    # 오전 9시 이전이면 어제 날짜 기준으로 보여줌
    if now_kst.hour < 9:
        today_date = today_date - timezone.timedelta(days=1)

    recommendations = DailyRecommendation.objects.filter(date=today_date)

    return render(request, 'screener/danta_list.html', {
        'recommendations': recommendations,
        'date': today_date
    })


def _parse_filter_date(raw_value):
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except (TypeError, ValueError):
        return None


def stats_list(request):
    """검색·필터·상세 조회를 제공하는 과거 단타 추천 성적 탭."""
    recommendations = DailyRecommendation.objects.all()

    query = request.GET.get('q', '').strip()[:50]
    status = request.GET.get('status', '').strip()
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
    if date_from:
        recommendations = recommendations.filter(date__gte=date_from)
    if date_to:
        recommendations = recommendations.filter(date__lte=date_to)

    recommendations = recommendations.order_by('-date', 'coin_ticker')

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
        'date_from': date_from_raw if date_from else '',
        'date_to': date_to_raw if date_to else '',
        'status_choices': DailyRecommendation.status_choices,
        'filter_query': query_params.urlencode(),
    })
