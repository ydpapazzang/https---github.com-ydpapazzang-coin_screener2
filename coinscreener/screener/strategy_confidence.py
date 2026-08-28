from datetime import timedelta

from django.utils import timezone

from .models import DailyRecommendation


ROUND_TRIP_COST_PCT = DailyRecommendation.ESTIMATED_ROUND_TRIP_COST_PCT


def _performance(rows):
    returns = [row['net_return'] for row in rows]
    count = len(returns)
    return {
        'sample_count': count,
        'expectancy_pct': round(sum(returns) / count, 2) if count else None,
        'cumulative_pct': round(sum(returns), 2) if count else None,
        'win_rate': round(
            sum(value > 0 for value in returns) / count * 100, 1
        ) if count else None,
    }


def _maximum_drawdown(returns):
    equity = peak = 100.0
    maximum = 0.0
    for value in returns:
        equity *= max(0.0, 1 + value / 100)
        peak = max(peak, equity)
        if peak:
            maximum = max(maximum, (peak - equity) / peak * 100)
    return round(maximum, 2)


def _maximum_loss_streak(returns):
    current = maximum = 0
    for value in returns:
        if value < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _grade(sample_count, span_days, expectancy, mdd, recent_90):
    if sample_count < 30:
        return '데이터 부족', f'최소 관찰 표본까지 {30 - sample_count}건 필요'
    if sample_count < 100:
        return '관찰', f'검증 단계까지 {100 - sample_count}건 필요'
    if sample_count < 300 or span_days < 365:
        missing = []
        if sample_count < 300:
            missing.append(f'표본 {300 - sample_count}건')
        if span_days < 365:
            missing.append(f'기간 {365 - span_days}일')
        return '검증 중', ' · '.join(missing) + ' 추가 필요'
    if expectancy <= 0:
        return '검증 중', '비용 후 기대수익이 0% 이하'
    if mdd > 20:
        return '검증 중', 'MDD가 20% 초과'
    if recent_90['sample_count'] < 20 or (recent_90['expectancy_pct'] or 0) <= 0:
        return '검증 중', '최근 90일 표본 또는 기대수익 기준 미충족'
    return '검증 완료', '표본·기간·기대수익·MDD·최근 성과 기준 통과'


def build_confidence_report(trade_type, today=None):
    """현재 기록 중인 최신 전략 버전만 대상으로 신뢰도를 계산한다."""
    today = today or timezone.localdate()
    all_records = DailyRecommendation.objects.filter(trade_type=trade_type)
    latest = all_records.exclude(strategy_version='').order_by(
        '-date', '-created_at', '-id'
    ).first()
    version = latest.strategy_version if latest else ''
    version_label = version or 'legacy (버전 기록 전)'
    base = all_records.exclude(status='skipped')
    versioned = base.filter(strategy_version=version)
    records = list(
        versioned.filter(result_pct__isnull=False)
        .order_by('date', 'id')
        .values('date', 'result_pct', 'market_regime')
    )
    rows = [
        {
            **record,
            'net_return': float(record['result_pct']) - ROUND_TRIP_COST_PCT,
            'regime': (record['market_regime'] or {}).get('label') or '미분류',
        }
        for record in records
    ]
    overall = _performance(rows)
    returns = [row['net_return'] for row in rows]
    span_days = (
        (rows[-1]['date'] - rows[0]['date']).days + 1 if rows else 0
    )
    periods = {}
    for days in (30, 90, 365):
        cutoff = today - timedelta(days=days - 1)
        periods[days] = _performance([
            row for row in rows if row['date'] >= cutoff
        ])

    regime_groups = {}
    for row in rows:
        regime_groups.setdefault(row['regime'], []).append(row)
    regimes = [
        {'label': label, **_performance(group)}
        for label, group in regime_groups.items()
    ]
    regimes.sort(key=lambda item: (-item['sample_count'], item['label']))

    mdd = _maximum_drawdown(returns)
    grade, grade_reason = _grade(
        overall['sample_count'], span_days,
        overall['expectancy_pct'] or 0, mdd, periods[90],
    )
    return {
        'trade_type': trade_type,
        'trade_type_label': dict(DailyRecommendation.trade_type_choices)[trade_type],
        'strategy_version': version_label,
        **overall,
        'span_days': span_days,
        'mdd_pct': mdd,
        'max_loss_streak': _maximum_loss_streak(returns),
        'periods': periods,
        'regimes': regimes[:5],
        'grade': grade,
        'grade_reason': grade_reason,
    }

