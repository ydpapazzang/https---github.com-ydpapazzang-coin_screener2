"""시세 캐시 신선도 판정.

오래된 OHLCV가 정상 신호처럼 사용되거나, 대규모 예약 스캔이 캐시 장애 때
외부 API 전체 재조회로 바뀌는 일을 막기 위한 공통 보호장치다.
"""
from django.utils import timezone

from .engine import max_cache_age
from .models import OHLCVCache


MINUTE_FLOOR_SECONDS = 20 * 60
MIN_FRESH_RATIO = 0.80


def allowed_cache_age(timeframe):
    """봉 주기별 예약 스캔 허용 캐시 나이(초)."""
    base = max_cache_age(timeframe)
    if timeframe.startswith('minute'):
        return max(MINUTE_FLOOR_SECONDS, base * 2)
    if timeframe == 'day':
        return 36 * 60 * 60
    return base * 2


def scan_freshness(tickers_data, conditions, now=None):
    """필요한 (티커, 타임프레임) 캐시의 신선 비율을 계산한다."""
    tickers = {
        item['ticker'] if isinstance(item, dict) else item
        for item in tickers_data
    }
    timeframes = {condition.timeframe for condition in conditions}
    expected = len(tickers) * len(timeframes)
    if not expected:
        return {
            'ok': False,
            'fresh_ratio': 0.0,
            'fresh': 0,
            'expected': expected,
            'stale_or_missing': expected,
            'latest_at': None,
        }

    now = now or timezone.now()
    fresh = 0
    latest_at = None
    rows = OHLCVCache.objects.filter(
        ticker__in=tickers,
        timeframe__in=timeframes,
    ).values('ticker', 'timeframe', 'updated_at')
    for row in rows:
        updated_at = row['updated_at']
        if latest_at is None or updated_at > latest_at:
            latest_at = updated_at
        age = (now - updated_at).total_seconds()
        if age <= allowed_cache_age(row['timeframe']):
            fresh += 1

    ratio = fresh / expected
    return {
        'ok': ratio >= MIN_FRESH_RATIO,
        'fresh_ratio': round(ratio * 100, 1),
        'fresh': fresh,
        'expected': expected,
        'stale_or_missing': expected - fresh,
        'latest_at': latest_at,
    }

