"""운영 서버 상태 수집 및 장애 알림 중복 억제."""
import json
import shutil
import socket
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import OHLCVCache


CACHE_CRITICAL_MINUTES = 60
MEMORY_CRITICAL_PCT = 8
MEMORY_WARNING_PCT = 15
DISK_CRITICAL_PCT = 10


def _linux_memory():
    path = Path('/proc/meminfo')
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        key, raw = line.split(':', 1)
        values[key] = int(raw.strip().split()[0]) * 1024
    total = values.get('MemTotal', 0)
    available = values.get('MemAvailable', 0)
    swap_total = values.get('SwapTotal', 0)
    swap_free = values.get('SwapFree', 0)
    return {
        'total_bytes': total,
        'available_bytes': available,
        'available_pct': round(available / total * 100, 1) if total else None,
        'swap_total_bytes': swap_total,
        'swap_used_bytes': max(0, swap_total - swap_free),
    }


def _web_is_listening(host='127.0.0.1', port=8000):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def collect_health(include_web=True):
    now = timezone.now()
    problems = []
    warnings = []

    database_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:
        database_ok = False
        problems.append(f'DB 연결 실패: {type(exc).__name__}')

    latest_cache = None
    cache_age_minutes = None
    if database_ok:
        latest_cache = OHLCVCache.objects.exclude(timeframe='RESULT').order_by(
            '-updated_at'
        ).values_list('updated_at', flat=True).first()
        if latest_cache:
            cache_age_minutes = round(
                (now - latest_cache).total_seconds() / 60, 1
            )
        if cache_age_minutes is None:
            problems.append('시세 캐시 데이터 없음')
        elif cache_age_minutes > CACHE_CRITICAL_MINUTES:
            problems.append(f'시세 캐시 {cache_age_minutes}분 지연')

    memory = _linux_memory()
    available_pct = memory.get('available_pct')
    if available_pct is not None:
        if available_pct < MEMORY_CRITICAL_PCT:
            problems.append(f'가용 메모리 {available_pct}%')
        elif available_pct < MEMORY_WARNING_PCT:
            warnings.append(f'가용 메모리 {available_pct}%')
        if not memory.get('swap_total_bytes'):
            warnings.append('Swap 비활성')

    disk = shutil.disk_usage(settings.BASE_DIR)
    disk_free_pct = round(disk.free / disk.total * 100, 1)
    if disk_free_pct < DISK_CRITICAL_PCT:
        problems.append(f'디스크 여유 {disk_free_pct}%')

    web_ok = None
    if include_web:
        web_ok = _web_is_listening()
        if not web_ok:
            problems.append('Gunicorn 127.0.0.1:8000 응답 없음')

    status = 'critical' if problems else ('warning' if warnings else 'ok')
    return {
        'status': status,
        'checked_at': timezone.localtime(now).isoformat(),
        'database_ok': database_ok,
        'web_ok': web_ok,
        'cache_age_minutes': cache_age_minutes,
        'memory': memory,
        'disk_free_pct': disk_free_pct,
        'problems': problems,
        'warnings': warnings,
    }


def _state_path():
    runtime = Path(settings.RUNTIME_DIR)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    return runtime / 'health-monitor.json'


def load_monitor_state():
    try:
        return json.loads(_state_path().read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_monitor_state(snapshot):
    path = _state_path()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding='utf-8'
    )
    temporary.replace(path)

