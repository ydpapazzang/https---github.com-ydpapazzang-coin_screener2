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


def _migrate_session_data_to_user(old_key, new_key):
    """익명 세션 시절 작성한 전략/즐겨찾기 데이터를 로그인한 사용자 키로 이전."""
    if not old_key or old_key == new_key:
        return
    try:
        from .models import Strategy, Favorite, ScanUsage, PaperPosition
        Strategy.objects.filter(owner_key=old_key).update(owner_key=new_key)
        for fav in Favorite.objects.filter(owner_key=old_key):
            if not Favorite.objects.filter(owner_key=new_key, exchange=fav.exchange, ticker=fav.ticker).exists():
                fav.owner_key = new_key
                fav.save(update_fields=['owner_key'])
            else:
                fav.delete()
        ScanUsage.objects.filter(owner_key=old_key).update(owner_key=new_key)
        PaperPosition.objects.filter(owner_key=old_key).update(owner_key=new_key)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Error migrating data from {old_key} to {new_key}: {e}")


def get_owner_key(request):
    """현재 세션/사용자의 owner_key를 반환.
    로그인한 사용자는 f"usr_{user.id}"를 사용하며, 기존 익명 세션의 데이터가 있다면 자동 이관한다."""
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        user_key = f"usr_{user.id}"
        old_session_key = request.session.get(SESSION_OWNER_KEY)
        if old_session_key and old_session_key != user_key:
            _migrate_session_data_to_user(old_session_key, user_key)
            request.session[SESSION_OWNER_KEY] = user_key
        return user_key

    key = request.session.get(SESSION_OWNER_KEY)
    if not key:
        key = uuid.uuid4().hex
        request.session[SESSION_OWNER_KEY] = key
        request.session.set_expiry(_ONE_YEAR)
    return key


def is_staff(request):
    """관리자(슈퍼유저/스태프)는 모든 전략을 소유자처럼 편집·관리할 수 있다.
    (텔레그램 알림 등 단일 봇 기반 기능은 원래 사이트 소유자 전용)"""
    user = getattr(request, 'user', None)
    return bool(user and user.is_authenticated and user.is_staff)


def is_sample(strategy):
    return not strategy.owner_key


def get_owned_strategy(request, strategy_id):
    """수정 가능한 전략만 반환. 관리자면 전체 허용, 아니면 내 소유만(샘플/타인은 404)."""
    if is_staff(request):
        return get_object_or_404(Strategy, id=strategy_id)
    key = get_owner_key(request)
    return get_object_or_404(Strategy, id=strategy_id, owner_key=key)


def get_viewable_strategy(request, strategy_id):
    """조회 가능한 전략(내 전략 또는 공용 샘플)만 반환. 타인의 개인 전략은 404. 관리자는 전체."""
    if is_staff(request):
        return get_object_or_404(Strategy, id=strategy_id)
    key = get_owner_key(request)
    strategy = get_object_or_404(Strategy, id=strategy_id)
    if strategy.owner_key and strategy.owner_key != key:
        raise Http404('접근할 수 없는 전략입니다.')
    return strategy


def my_and_sample_strategies(request):
    """(내 전략 QS, 공용 샘플 QS) 반환. 관리자는 전체를 '내 전략'으로 본다."""
    if is_staff(request):
        return Strategy.objects.all().order_by('-created_at'), Strategy.objects.none()
    key = get_owner_key(request)
    mine = Strategy.objects.filter(owner_key=key).order_by('-created_at')
    samples = Strategy.objects.filter(owner_key__isnull=True).order_by('created_at')
    return mine, samples
