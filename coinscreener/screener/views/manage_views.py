"""PC 관리자용 백오피스 (/manage/).

Django 슈퍼유저 로그인(staff)만 접근 가능. 로그인 안 되어 있으면 /admin/login/ 으로 이동.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Count, Sum, Avg
from django.db.models.functions import TruncDate

from ..models import (
    AlertHistory, DailyRecommendation, VisitLog, OHLCVCache, Strategy,
)


def _danta_stats(qs):
    """단타 추천 QuerySet에서 승/패/승률/누적수익률 요약."""
    total = qs.count()
    wins = qs.filter(status='success').count()
    losses = qs.filter(status='failed').count()
    decided = wins + losses
    win_rate = round(wins / decided * 100, 1) if decided else 0.0
    cum = qs.filter(result_pct__isnull=False).aggregate(s=Sum('result_pct'))['s'] or 0.0
    return {
        'total': total, 'wins': wins, 'losses': losses,
        'win_rate': win_rate, 'cum_pct': round(cum, 2),
    }


@staff_member_required
def manage_dashboard(request):
    now = timezone.localtime()
    today = timezone.localdate()

    # ── 알람 ──
    alerts_today = AlertHistory.objects.filter(created_at__date=today).count()
    alerts_total = AlertHistory.objects.count()

    # ── 단타 ──
    danta_all = _danta_stats(DailyRecommendation.objects.all())
    danta_today_qs = DailyRecommendation.objects.filter(date=today)
    danta_today = danta_today_qs.count()

    # ── 방문 ──
    visits_today = VisitLog.objects.filter(created_at__date=today).count()
    visits_uniq_today = (
        VisitLog.objects.filter(created_at__date=today)
        .values('ip').distinct().count()
    )
    visits_total = VisitLog.objects.count()

    # ── 서버/크롤러 상태 ──
    latest_cache = OHLCVCache.objects.order_by('-updated_at').first()
    if latest_cache:
        mins = (now - timezone.localtime(latest_cache.updated_at)).total_seconds() / 60
        crawler_mins = round(mins, 1)
        crawler_stale = mins > 20  # 5분 주기인데 20분 넘으면 이상
    else:
        crawler_mins = None
        crawler_stale = True
    cache_count = OHLCVCache.objects.count()
    cache_tickers = OHLCVCache.objects.values('ticker').distinct().count()

    last_pick = DailyRecommendation.objects.order_by('-date').values_list('date', flat=True).first()

    ctx = {
        'now': now,
        'alerts_today': alerts_today,
        'alerts_total': alerts_total,
        'danta': danta_all,
        'danta_today': danta_today,
        'visits_today': visits_today,
        'visits_uniq_today': visits_uniq_today,
        'visits_total': visits_total,
        'crawler_mins': crawler_mins,
        'crawler_stale': crawler_stale,
        'cache_count': cache_count,
        'cache_tickers': cache_tickers,
        'last_pick': last_pick,
        'active': 'dashboard',
    }
    return render(request, 'screener/manage/dashboard.html', ctx)


@staff_member_required
def manage_alerts(request):
    qs = AlertHistory.objects.select_related('strategy').all()

    strategy_id = request.GET.get('strategy')
    if strategy_id:
        qs = qs.filter(strategy_id=strategy_id)

    days = request.GET.get('days')
    if days and days.isdigit():
        since = timezone.now() - timezone.timedelta(days=int(days))
        qs = qs.filter(created_at__gte=since)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))

    ctx = {
        'page': page,
        'strategies': Strategy.objects.all(),
        'cur_strategy': strategy_id or '',
        'cur_days': days or '',
        'active': 'alerts',
    }
    return render(request, 'screener/manage/alerts.html', ctx)


@staff_member_required
def manage_danta(request):
    qs = DailyRecommendation.objects.all()

    status = request.GET.get('status')
    if status:
        qs = qs.filter(status=status)

    days = request.GET.get('days')
    if days and days.isdigit():
        since = timezone.localdate() - timezone.timedelta(days=int(days))
        qs = qs.filter(date__gte=since)

    stats = _danta_stats(qs)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))

    ctx = {
        'page': page,
        'stats': stats,
        'status_choices': DailyRecommendation.status_choices,
        'cur_status': status or '',
        'cur_days': days or '',
        'active': 'danta',
    }
    return render(request, 'screener/manage/danta.html', ctx)


@staff_member_required
def manage_visits(request):
    days = request.GET.get('days', '14')
    days = int(days) if days.isdigit() else 14
    since = timezone.now() - timezone.timedelta(days=days)

    base = VisitLog.objects.filter(created_at__gte=since)

    # 일별 방문수 / 순방문(IP)
    daily = list(
        base.annotate(d=TruncDate('created_at'))
        .values('d')
        .annotate(c=Count('id'), u=Count('ip', distinct=True))
        .order_by('d')
    )
    max_c = max((row['c'] for row in daily), default=0)

    # 인기 경로 Top 10
    top_paths = list(
        base.values('path').annotate(c=Count('id')).order_by('-c')[:10]
    )

    # 요약
    total_visits = base.count()
    uniq_ips = base.values('ip').distinct().count()

    # 최근 방문 로그
    paginator = Paginator(VisitLog.objects.all(), 100)
    page = paginator.get_page(request.GET.get('page'))

    ctx = {
        'daily': daily,
        'max_c': max_c,
        'top_paths': top_paths,
        'total_visits': total_visits,
        'uniq_ips': uniq_ips,
        'days': days,
        'page': page,
        'active': 'visits',
    }
    return render(request, 'screener/manage/visits.html', ctx)
