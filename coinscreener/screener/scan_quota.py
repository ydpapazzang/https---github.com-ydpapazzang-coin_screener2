"""일일 무료 스캔 계측과 Rewarded Ad 조회권 관리."""
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ScanUsage


def quota_is_enforced():
    """실제 보상형 광고 단위가 있을 때만 조회 제한을 활성화한다."""
    return bool(settings.REWARDED_AD_UNIT_PATH)


def _snapshot(usage, bypass=False):
    free_limit = settings.SCAN_DAILY_FREE_LIMIT
    allowance = free_limit + usage.reward_credits
    enforced = quota_is_enforced() and not bypass
    remaining = max(0, allowance - usage.scan_count)
    return {
        'date': usage.date.isoformat(),
        'scan_count': usage.scan_count,
        'free_limit': free_limit,
        'reward_credits': usage.reward_credits,
        'remaining': remaining,
        'enforced': enforced,
        'allowed': (not enforced) or remaining > 0,
        'reward_available': bool(settings.REWARDED_AD_UNIT_PATH),
    }


def get_scan_quota(owner_key, *, bypass=False, date=None):
    date = date or timezone.localdate()
    usage, _ = ScanUsage.objects.get_or_create(owner_key=owner_key, date=date)
    return _snapshot(usage, bypass=bypass)


@transaction.atomic
def consume_scan(owner_key, *, bypass=False, date=None):
    """허용된 스캔 1회를 원자적으로 차감하고 최신 상태를 반환한다."""
    date = date or timezone.localdate()
    usage, _ = ScanUsage.objects.select_for_update().get_or_create(
        owner_key=owner_key,
        date=date,
    )
    before = _snapshot(usage, bypass=bypass)
    if not before['allowed']:
        return before
    usage.scan_count += 1
    usage.save(update_fields=['scan_count', 'updated_at'])
    result = _snapshot(usage, bypass=bypass)
    result['consumed'] = True
    return result


@transaction.atomic
def grant_reward_credit(owner_key, *, date=None):
    """광고 완료 1회에 스캔 조회권 1회를 추가한다."""
    date = date or timezone.localdate()
    usage, _ = ScanUsage.objects.select_for_update().get_or_create(
        owner_key=owner_key,
        date=date,
    )
    if usage.reward_credits >= settings.SCAN_DAILY_REWARD_LIMIT:
        result = _snapshot(usage)
        result['granted'] = False
        result['reason'] = 'daily_reward_limit'
        return result
    usage.reward_credits += 1
    usage.save(update_fields=['reward_credits', 'updated_at'])
    result = _snapshot(usage)
    result['granted'] = True
    return result

