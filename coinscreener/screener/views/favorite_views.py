import json
import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.core.cache import cache

from ..models import Favorite, MarketData
from ..ownership import get_owner_key
from .scan_views import _enrich_upbit_ticker_snapshot

logger = logging.getLogger(__name__)


def _parse_body(request):
    """POST 요청에서 JSON 바디 또는 form-data 파싱."""
    if request.content_type == 'application/json':
        try:
            return json.loads(request.body.decode('utf-8'))
        except (ValueError, TypeError):
            return {}
    return request.POST


def _safe_float(val):
    if val in (None, '', 'null'):
        return None
    try:
        f = float(str(val).replace(',', '').strip())
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def favorite_list(request):
    """즐겨찾기 종목 목록 페이지. 실시간 현재가와 등락률을 함께 렌더링."""
    owner_key = get_owner_key(request)
    selected_exchange = request.GET.get('exchange', '').strip()

    qs = Favorite.objects.filter(owner_key=owner_key)
    if selected_exchange in ('upbit', 'kospi'):
        qs = qs.filter(exchange=selected_exchange)

    favorites = list(qs)

    # 업비트 종목들은 실시간 시세 스냅샷 보강
    upbit_items = [
        {'ticker': fav.ticker, 'name': fav.name, 'fav_obj': fav}
        for fav in favorites if fav.exchange == 'upbit'
    ]
    if upbit_items:
        _enrich_upbit_ticker_snapshot(upbit_items)

    # 렌더링용 데이터 맵핑
    enriched_favorites = []
    for fav in favorites:
        item_data = {
            'id': fav.id,
            'ticker': fav.ticker,
            'name': fav.name,
            'exchange': fav.exchange,
            'memo': fav.memo,
            'target_price': fav.target_price,
            'created_at': fav.created_at,
            'current_price': None,
            'change_rate': None,
        }
        # 스냅샷에서 가격 주입
        matching = next((u for u in upbit_items if u['ticker'] == fav.ticker), None)
        if matching:
            item_data['current_price'] = matching.get('current_price')
            item_data['change_rate'] = matching.get('change_rate')
            item_data['amount'] = matching.get('amount')

        enriched_favorites.append(item_data)

    return render(request, 'screener/favorite_list.html', {
        'favorites': enriched_favorites,
        'selected_exchange': selected_exchange,
        'total_count': len(enriched_favorites),
    })


@require_POST
def favorite_toggle(request):
    """검색 결과 및 상세 화면에서 별표(★) 클릭 시 즐겨찾기 즉시 토글 API."""
    owner_key = get_owner_key(request)
    body = _parse_body(request)

    ticker = (body.get('ticker') or '').strip()
    name = (body.get('name') or '').strip() or ticker
    exchange = (body.get('exchange') or 'upbit').strip().lower()

    if not ticker:
        return JsonResponse({'ok': False, 'error': '종목 코드가 필요합니다.'}, status=400)

    # 이미 등록되어 있는지 확인
    fav = Favorite.objects.filter(
        owner_key=owner_key, exchange=exchange, ticker=ticker
    ).first()

    if fav:
        fav.delete()
        return JsonResponse({
            'ok': True,
            'favorited': False,
            'ticker': ticker,
            'message': f"{name} 즐겨찾기에서 해제되었습니다.",
        })
    else:
        Favorite.objects.create(
            owner_key=owner_key,
            exchange=exchange,
            ticker=ticker,
            name=name,
        )
        return JsonResponse({
            'ok': True,
            'favorited': True,
            'ticker': ticker,
            'message': f"{name} 즐겨찾기에 등록되었습니다.",
        })


@require_POST
def favorite_add(request):
    """즐겨찾기 화면 모달에서 종목 직접 추가."""
    owner_key = get_owner_key(request)
    body = _parse_body(request)

    ticker = (body.get('ticker') or '').strip()
    name = (body.get('name') or '').strip()
    exchange = (body.get('exchange') or 'upbit').strip().lower()
    memo = (body.get('memo') or '').strip()
    target_price = _safe_float(body.get('target_price'))

    if not ticker:
        return JsonResponse({'ok': False, 'error': '종목 코드를 선택하세요.'}, status=400)

    if not name:
        # DB의 MarketData에서 이름 조회 시도
        md = MarketData.objects.filter(exchange=exchange, ticker=ticker).first()
        name = md.name if md else ticker

    fav, created = Favorite.objects.update_or_create(
        owner_key=owner_key,
        exchange=exchange,
        ticker=ticker,
        defaults={
            'name': name,
            'memo': memo,
            'target_price': target_price,
        }
    )

    action_text = "등록" if created else "수정"
    return JsonResponse({
        'ok': True,
        'favorited': True,
        'favorite_id': fav.id,
        'message': f"{name} 즐겨찾기에 {action_text}되었습니다.",
    })


@require_POST
def favorite_update(request, favorite_id):
    """즐겨찾기 종목의 메모 및 목표가 수정."""
    owner_key = get_owner_key(request)
    fav = get_object_or_404(Favorite, id=favorite_id, owner_key=owner_key)
    body = _parse_body(request)

    if 'memo' in body:
        fav.memo = (body.get('memo') or '').strip()
    if 'target_price' in body:
        fav.target_price = _safe_float(body.get('target_price'))

    fav.save()
    return JsonResponse({
        'ok': True,
        'message': f"{fav.name} 설정이 수정되었습니다.",
        'memo': fav.memo,
        'target_price': fav.target_price,
    })


@require_POST
def favorite_delete(request, favorite_id):
    """즐겨찾기 종목 삭제."""
    owner_key = get_owner_key(request)
    fav = get_object_or_404(Favorite, id=favorite_id, owner_key=owner_key)
    name = fav.name
    fav.delete()
    return JsonResponse({
        'ok': True,
        'message': f"{name} 즐겨찾기에서 삭제되었습니다.",
    })


@require_GET
def favorite_search_tickers(request):
    """종목 직접 추가 모달용 검색 자동완성 API."""
    q = (request.GET.get('q') or '').strip()
    exchange = (request.GET.get('exchange') or 'upbit').strip().lower()

    if not q:
        return JsonResponse({'ok': True, 'tickers': []})

    qs = MarketData.objects.filter(exchange=exchange)
    from django.db.models import Q
    qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q)).order_by('-amount')[:20]

    results = [
        {
            'ticker': item.ticker,
            'name': item.name,
            'exchange': item.exchange,
            'display_ticker': item.ticker.replace('KRW-', '') if item.exchange == 'upbit' else item.ticker,
        }
        for item in qs
    ]

    return JsonResponse({'ok': True, 'tickers': results})
