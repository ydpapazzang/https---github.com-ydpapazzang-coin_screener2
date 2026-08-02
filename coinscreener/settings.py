"""
Django settings for coinscreener project.
Django 4.2.0
"""
import dj_database_url
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 파일 로드 (서버 환경변수 세팅용)
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# SECRET_KEY는 환경변수 설정을 권장한다.
#  - DEBUG=True : 안전하지 않은 개발용 고정 fallback 사용
#  - DEBUG=False & 미설정 : 기동을 막지 않도록 런타임 랜덤 키를 생성하되 경고를 남긴다.
#    (재시작마다 키가 바뀌어 기존 세션/CSRF 토큰이 무효화되므로 운영에선 반드시 env로 고정할 것)
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-local-dev-only-change-in-production'
    else:
        from django.core.management.utils import get_random_secret_key
        SECRET_KEY = get_random_secret_key()
        import logging
        logging.getLogger(__name__).warning(
            'DJANGO_SECRET_KEY 환경변수가 없어 임시 랜덤 키로 기동합니다. '
            '재시작마다 세션/CSRF가 무효화되니 .env에 DJANGO_SECRET_KEY를 설정하세요.'
        )

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,.duckdns.org').split(',')

# HTTPS 환경(DuckDNS 등)에서 POST 요청 시 CSRF 에러 방지
CSRF_TRUSTED_ORIGINS = [
    'https://*.duckdns.org',
]

# Nginx 등 리버스 프록시 뒤에서 동작할 때 HTTPS 인식
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'coinscreener.screener',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # 백오피스 방문 기록 (페이지뷰만 가볍게 기록)
    'coinscreener.screener.middleware.VisitLogMiddleware',
]

ROOT_URLCONF = 'coinscreener.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'coinscreener.wsgi.application'

# ────────────────────────────────────────────
# DB 설정 (오류 수정 버전)
# ────────────────────────────────────────────
# DATABASE_URL 환경변수가 있으면 외부 DB(PostgreSQL 등) 연동 / 없으면 로컬 SQLite fallback
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    # 로컬에서 작업할 때는 편하게 SQLite가 돌아가도록 방어 코드 추가
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
        'OPTIONS': {
            'MAX_ENTRIES': 10000
        }
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True

# ────────────────────────────────────────────
# 정적 파일 설정
# ────────────────────────────────────────────
STATIC_URL = 'static/'
# collectstatic 시 정적 파일들을 한곳에 모아 WhiteNoise로 서빙
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

