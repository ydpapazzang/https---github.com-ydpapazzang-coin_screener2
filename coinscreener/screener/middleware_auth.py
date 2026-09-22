"""비인가자 접근 전면 차단 미들웨어 (PrivateAccessMiddleware).

- 로그인하지 않은 사용자는 서비스 전체(홈, 전략, 검색, 즐겨찾기 등)에 접근할 수 없으며 /login/ 으로 강제 이동.
- 로그인했으나 관리자 승인을 받지 못한 사용자(is_approved=False)는 /auth/pending/ 페이지로 이동.
- 슈퍼유저/스태프 및 승인 완료된 사용자만 서비스 이용 가능.
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings


class PrivateAccessMiddleware:
    WHITELIST_PREFIXES = (
        '/login/',
        '/auth/',
        '/healthz/',
        '/static/',
        '/admin/',
        '/favicon.ico',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'PRIVATE_ACCESS_ENABLED', True):
            return self.get_response(request)

        path = request.path

        # 1. 화이트리스트 경로는 검사 없이 통과
        for prefix in self.WHITELIST_PREFIXES:
            if path.startswith(prefix):
                return self.get_response(request)

        user = getattr(request, 'user', None)

        # 2. 미로그인 사용자는 로그인 페이지로 리다이렉트
        if not user or not user.is_authenticated:
            login_url = reverse('login')
            if path != '/':
                return redirect(f"{login_url}?next={path}")
            return redirect(login_url)

        # 3. 관리자(슈퍼유저 또는 스태프)는 즉시 통과
        if user.is_superuser or user.is_staff:
            return self.get_response(request)

        # 4. 일반 사용자 승인 여부 확인
        profile = getattr(user, 'profile', None)
        if not profile:
            # 프로필이 없으면 자동 생성 후 기본 False
            from .models import UserProfile
            profile, _ = UserProfile.objects.get_or_create(user=user)

        if profile.is_approved:
            return self.get_response(request)

        # 5. 승인되지 않은 사용자는 대기 페이지로 이동
        return redirect('auth_pending')
