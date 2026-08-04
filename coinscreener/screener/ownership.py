"""세션 기반 익명 소유권 헬퍼.

가입 없이 브라우저 세션마다 고유 owner_key를 부여해 '내 전략'을 분리한다.
 - owner_key 가 있는 Strategy = 특정 세션의 개인 전략
 - owner_key 가 비어있는(NULL/'') Strategy = 공용 샘플(읽기전용, 누구나 보고 복제 가능)
"""
import uuid
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Strategy

SESSION_OWNER_KEY = 'owner_key'
_ONE_YEAR = 60 * 60 * 24 * 365


def get_owner_key(request):
    """현재 세션의 owner_key를 반환(없으면 생성)."""
    key = request.session.get(SESSION_OWNER_KEY)
    if not key:
        key = uuid.uuid4().hex
        request.session[SESSION_OWNER_KEY] = key
        request.session.set_expiry(_ONE_YEAR)
    return key


def is_sample(strategy):
    return not strategy.owner_key


def get_owned_strategy(request, strategy_id):
    """수정 가능한(= 내가 소유한) 전략만 반환. 샘플/타인 전략은 404."""
    key = get_owner_key(request)
    return get_object_or_404(Strategy, id=strategy_id, owner_key=key)


def get_viewable_strategy(request, strategy_id):
    """조회 가능한 전략(내 전략 또는 공용 샘플)만 반환. 타인의 개인 전략은 404."""
    key = get_owner_key(request)
    strategy = get_object_or_404(Strategy, id=strategy_id)
    if strategy.owner_key and strategy.owner_key != key:
        raise Http404('접근할 수 없는 전략입니다.')
    return strategy


def my_and_sample_strategies(request):
    """(내 전략 QS, 공용 샘플 QS) 반환."""
    key = get_owner_key(request)
    mine = Strategy.objects.filter(owner_key=key).order_by('-created_at')
    samples = Strategy.objects.filter(owner_key__isnull=True).order_by('created_at')
    return mine, samples
