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
from ..system_health import collect_health


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
    system_health = collect_health(include_web=False)

    # ── 알람 ──
    alerts_today = AlertHistory.objects.filter(created_at__date=today).count()
    alerts_total = AlertHistory.objects.count()

    # ── 단타 ──
    danta_all = _danta_stats(DailyRecommendation.objects.filter(trade_type='danta'))
    danta_today_qs = DailyRecommendation.objects.filter(date=today, trade_type='danta')
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

    last_pick = DailyRecommendation.objects.filter(trade_type='danta').order_by('-date').values_list('date', flat=True).first()

    # ─────────── 차트 데이터 (Chart.js, 브라우저 렌더링) ───────────
    chart_days = 14
    since = timezone.now() - timezone.timedelta(days=chart_days)
    date_range = [today - timezone.timedelta(days=i) for i in range(chart_days - 1, -1, -1)]
    day_labels = [d.strftime('%m/%d') for d in date_range]

    # 방문 추이 (봇 404 제외: 2xx/3xx 만)
    visit_daily = (
        VisitLog.objects.filter(created_at__gte=since, status_code__lt=400)
        .annotate(d=TruncDate('created_at')).values('d')
        .annotate(pv=Count('id'), uv=Count('ip', distinct=True))
    )
    vmap = {row['d']: row for row in visit_daily}
    pv_series = [vmap.get(d, {}).get('pv', 0) for d in date_range]
    uv_series = [vmap.get(d, {}).get('uv', 0) for d in date_range]

    # 알람 발생 추이
    alert_daily = (
        AlertHistory.objects.filter(created_at__gte=since)
        .annotate(d=TruncDate('created_at')).values('d').annotate(c=Count('id'))
    )
    amap = {row['d']: row['c'] for row in alert_daily}
    alert_series = [amap.get(d, 0) for d in date_range]

    # 단타 누적 수익률 곡선 (확정 손익만, 날짜순 누적)
    decided = (
        DailyRecommendation.objects.filter(trade_type='danta', result_pct__isnull=False)
        .order_by('date').values('date', 'result_pct')
    )
    day_sum = {}
    for row in decided:
        day_sum[row['date']] = day_sum.get(row['date'], 0.0) + (row['result_pct'] or 0.0)
    equity_labels, equity_series, _cum = [], [], 0.0
    for d in sorted(day_sum):
        _cum += day_sum[d]
        equity_labels.append(d.strftime('%m/%d'))
        equity_series.append(round(_cum, 2))

    # 단타 승/패 도넛 + 상태 분포 도넛
    winloss_series = [danta_all['wins'], danta_all['losses']]
    smap = dict(
        DailyRecommendation.objects.filter(trade_type='danta').values('status')
        .annotate(c=Count('id')).values_list('status', 'c')
    )
    status_labels = [lbl for val, lbl in DailyRecommendation.status_choices]
    status_series = [smap.get(val, 0) for val, lbl in DailyRecommendation.status_choices]

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
        'system_health': system_health,
        'active': 'dashboard',
        # 차트
        'day_labels': day_labels,
        'pv_series': pv_series,
        'uv_series': uv_series,
        'alert_series': alert_series,
        'equity_labels': equity_labels,
        'equity_series': equity_series,
        'winloss_series': winloss_series,
        'status_labels': status_labels,
        'status_series': status_series,
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
    qs = DailyRecommendation.objects.filter(trade_type='danta')

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

