import logging
import urllib.parse
import uuid
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import UserProfile
from .. import telegram as tg

logger = logging.getLogger(__name__)


def login_view(request):
    """로그인 화면. 구글 간편 로그인 및 관리자 비밀번호 로그인 제공."""
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        profile = getattr(user, 'profile', None)
        if user.is_staff or (profile and profile.is_approved):
            return redirect('/')
        return redirect('auth_pending')

    next_url = request.GET.get('next', '/')
    if request.method == 'POST':
        # 관리자 계정 아이디/비밀번호 직접 로그인
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        auth_user = authenticate(request, username=username, password=password)
        if auth_user is not None:
            login(request, auth_user)
            # 프로필 없으면 생성
            profile, _ = UserProfile.objects.get_or_create(user=auth_user)
            if auth_user.is_staff or auth_user.is_superuser:
                if not profile.is_approved:
                    profile.is_approved = True
                    profile.save()
                return redirect(next_url or '/')
            if profile.is_approved:
                return redirect(next_url or '/')
            return redirect('auth_pending')
        else:
            messages.error(request, '아이디 또는 비밀번호가 올바르지 않습니다.')

    ctx = {
        'next': next_url,
        'has_google': bool(getattr(settings, 'GOOGLE_CLIENT_ID', '')),
    }
    return render(request, 'screener/login.html', ctx)


def google_login(request):
    """구글 OAuth 2.0 인증 페이지로 리다이렉트."""
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '').strip()
    if not client_id:
        messages.error(request, '구글 로그인 설정(GOOGLE_CLIENT_ID)이 아직 완료되지 않았습니다. 관리자 계정으로 로그인해 주세요.')
        return redirect('login')

    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    # Nginx/GCP 프록시 환경에서 https 보장
    if not redirect_uri.startswith('https://') and 'localhost' not in redirect_uri and '127.0.0.1' not in redirect_uri:
        redirect_uri = redirect_uri.replace('http://', 'https://', 1)

    state = uuid.uuid4().hex
    request.session['google_oauth_state'] = state
    request.session['login_next'] = request.GET.get('next', '/')

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'prompt': 'select_account',
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return redirect(url)


def google_callback(request):
    """구글 OAuth 2.0 콜백 처리."""
    code = request.GET.get('code')
    state = request.GET.get('state')
    expected_state = request.session.pop('google_oauth_state', None)

    if not code:
        messages.error(request, '구글 인증 코드를 수신하지 못했습니다.')
        return redirect('login')

    if expected_state and state != expected_state:
        messages.error(request, '인증 상태 검증(State)에 실패했습니다. 다시 시도해주세요.')
        return redirect('login')

    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '').strip()
    client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', '').strip()
    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    if not redirect_uri.startswith('https://') and 'localhost' not in redirect_uri and '127.0.0.1' not in redirect_uri:
        redirect_uri = redirect_uri.replace('http://', 'https://', 1)

    # 1. Access Token 발급 요청
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }
    try:
        token_resp = requests.post(token_url, data=token_data, timeout=10)
        if not token_resp.ok:
            logger.error(f"Google token error: {token_resp.text}")
            messages.error(request, '구글 인증 토큰 발급에 실패했습니다.')
            return redirect('login')
        tokens = token_resp.json()
        access_token = tokens.get('access_token')
    except Exception as e:
        logger.error(f"Google token request exception: {e}")
        messages.error(request, '구글 서버와의 통신에 실패했습니다.')
        return redirect('login')

    # 2. 프로필 정보 요청
    try:
        userinfo_resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={'Authorization': f"Bearer {access_token}"},
            timeout=10,
        )
        if not userinfo_resp.ok:
            messages.error(request, '구글 프로필 정보를 불러오지 못했습니다.')
            return redirect('login')
        info = userinfo_resp.json()
    except Exception as e:
        logger.error(f"Google userinfo request exception: {e}")
        messages.error(request, '구글 사용자 정보 요청 중 오류가 발생했습니다.')
        return redirect('login')

    email = (info.get('email') or '').strip().lower()
    name = (info.get('name') or info.get('given_name') or email.split('@')[0]).strip()
    if not email:
        messages.error(request, '구글 계정의 이메일 정보를 확인할 수 없습니다.')
        return redirect('login')

    User = get_user_model()
    user = User.objects.filter(email__iexact=email).first()
    is_new = False
    if not user:
        is_new = True
        # 유니크한 username 생성
        base_username = email.split('@')[0][:25]
        username = base_username
        cnt = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{cnt}"
            cnt += 1
        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=name[:30],
        )

    profile, _ = UserProfile.objects.get_or_create(user=user)

    # 3. 관리자 지정 및 자동 승인 판정
    admin_email = getattr(settings, 'ADMIN_EMAIL', '').strip().lower()
    is_admin = False
    if admin_email and email == admin_email:
        is_admin = True
    elif user.is_superuser:
        is_admin = True
    elif User.objects.count() == 1:
        # 최초 1인 계정 자동 관리자
        is_admin = True

    if is_admin:
        profile.is_approved = True
        profile.approved_at = timezone.now()
        profile.approved_by = 'SYSTEM_ADMIN'
        profile.save()
        user.is_staff = True
        user.is_superuser = True
        user.save()
    elif is_new:
        profile.is_approved = False
        profile.save()
        # 텔레그램으로 승인 요청 알림 발송
        if tg.is_configured():
            try:
                tg.send_text_alert(
                    f"🔔 <b>[신규 회원가입 승인 요청]</b>\n"
                    f"• 이메일: <code>{email}</code>\n"
                    f"• 이름: {name}\n"
                    f"• 승인 관리: https://woniiscreener.duckdns.org/manage/users/"
                )
            except Exception as tg_err:
                logger.warning(f"Failed to send telegram approval alert: {tg_err}")

    # 로그인 처리
    login(request, user)

    if not profile.is_approved and not user.is_staff:
        return redirect('auth_pending')

    next_url = request.session.pop('login_next', '/')
    return redirect(next_url or '/')


def pending_approval(request):
    """관리자 승인 대기 화면."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return redirect('login')

    profile = getattr(user, 'profile', None)
    if user.is_staff or (profile and profile.is_approved):
        return redirect('/')

    return render(request, 'screener/pending_approval.html', {'user': user})


def logout_view(request):
    """로그아웃 처리."""
    logout(request)
    return redirect('login')


@staff_member_required
def manage_users(request):
    """관리자용 회원 목록 및 승인 관리 (/manage/users/)."""
    User = get_user_model()
    users = User.objects.select_related('profile').all().order_by('-date_joined')
    ctx = {
        'users': users,
        'active': 'users',
    }
    return render(request, 'screener/manage/users.html', ctx)


@staff_member_required
@require_POST
def toggle_user_approval(request, user_id):
    """회원 승인 / 승인 취소 토글 API."""
    User = get_user_model()
    target_user = get_object_or_404(User, id=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    # 본인 계정은 취소 불가 방어
    if target_user == request.user:
        return JsonResponse({'ok': False, 'error': '본인 계정의 승인 상태는 변경할 수 없습니다.'}, status=400)

    profile.is_approved = not profile.is_approved
    if profile.is_approved:
        profile.approved_at = timezone.now()
        profile.approved_by = request.user.username or request.user.email
    else:
        profile.approved_at = None
        profile.approved_by = ''
    profile.save()

    return JsonResponse({
        'ok': True,
        'is_approved': profile.is_approved,
        'user_id': target_user.id,
        'username': target_user.username,
        'email': target_user.email,
    })
