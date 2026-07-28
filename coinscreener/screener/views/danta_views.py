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

def stats_list(request):
    """과거 단타 추천 성적 탭"""
    # 오늘 추천은 제외하거나 포함할 수 있음. 보통 결과가 나온 것들을 위주로 보여줌
    recommendations = DailyRecommendation.objects.all().order_by('-date', 'coin_ticker')
    
    # 요약 통계 계산
    total = recommendations.count()
    wins = recommendations.filter(status='success').count()
    losses = recommendations.filter(status='failed').count()
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    
    return render(request, 'screener/stats_list.html', {
        'recommendations': recommendations,
        'total': total,
        'wins': wins,
        'losses': losses,
        'win_rate': round(win_rate, 1)
    })
