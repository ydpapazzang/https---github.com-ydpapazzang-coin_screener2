"""공개 검색의 중복 실행을 막는 DB 기반 lease."""
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import ScanLease


def acquire_scan_lease(owner_key, now=None):
    """실행권을 얻으면 토큰, 이미 실행 중이면 None을 반환한다."""
    now = now or timezone.now()
    token = uuid.uuid4().hex
    expires_at = now + timedelta(seconds=settings.SCAN_LEASE_SECONDS)

    # 비정상 종료로 남은 만료 lease는 조건부 UPDATE 한 번으로 회수한다.
    claimed = ScanLease.objects.filter(
        owner_key=owner_key,
        expires_at__lte=now,
    ).update(token=token, acquired_at=now, expires_at=expires_at)
    if claimed:
        return token

    try:
        # 호출자가 이미 transaction.atomic() 안에 있어도 중복 키 실패가 바깥
        # 트랜잭션을 오염시키지 않도록 savepoint를 만든다.
        with transaction.atomic():
            ScanLease.objects.create(
                owner_key=owner_key,
                token=token,
                acquired_at=now,
                expires_at=expires_at,
            )
        return token
    except IntegrityError:
        # 다른 요청이 먼저 같은 owner_key를 생성했다.
        return None


def release_scan_lease(owner_key, token):
    """자신이 획득한 실행권만 해제해 새 요청의 lease를 지우지 않는다."""
    if not token:
        return False
    deleted, _ = ScanLease.objects.filter(
        owner_key=owner_key,
        token=token,
    ).delete()
    return bool(deleted)

