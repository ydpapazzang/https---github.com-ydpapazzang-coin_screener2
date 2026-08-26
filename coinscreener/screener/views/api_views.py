import os
import json
import logging
import traceback
import concurrent.futures
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponseForbidden, HttpResponse
from django.utils import timezone
from django.contrib import messages
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
import pyupbit

from ..models import Strategy, Condition, AlertSetting, AlertHistory, OHLCVCache
from ..engine import check_strategy
from ..system_health import collect_health

logger = logging.getLogger(__name__)

def _get_cron_secret():
    return os.environ.get('CRON_SECRET', '')


@require_GET
def healthz(request):
    """로드밸런서·외부 모니터용 경량 상태 확인."""
    snapshot = collect_health(include_web=False)
    payload = {
        'ok': snapshot['status'] != 'critical',
        'status': snapshot['status'],
        'checked_at': snapshot['checked_at'],
        'database_ok': snapshot['database_ok'],
        'cache_age_minutes': snapshot['cache_age_minutes'],
    }
    return JsonResponse(
        payload,
        status=503 if snapshot['status'] == 'critical' else 200,
    )

