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
from ..ownership import get_owned_strategy, get_viewable_strategy
from .. import telegram as tg

logger = logging.getLogger(__name__)

def _get_cron_secret():
    return os.environ.get('CRON_SECRET', '')


# ─────────────────────────────────────────────────────────────
# (A) 인프로세스 파싱 캐시
#   (ticker, timeframe) -> (updated_at, DataFrame)
#   크롤러가 5분 주기로 OHLCVCache를 갱신하므로, updated_at이 바뀌기 전까지
#   JSON→DataFrame 재파싱을 건너뛴다. runserver가 단일 장수 프로세스라
#   검색 요청 간에 파싱 결과가 재사용된다(= 반복 검색이 매우 빨라짐).
# ─────────────────────────────────────────────────────────────
import threading
_PARSED_OHLCV = {}
_PARSED_OHLCV_LOCK = threading.Lock()
_PARSED_OHLCV_MAX = 4000  # 티커×타임프레임 상한(메모리 방어)


# (B) 상호작용 검색 기본 스캔 범위
#   vol_limit=0(전체)로 들어와도, 거래대금 정렬을 신뢰할 수 있는 거래소는
#   상위 N개만 스캔해 속도를 높인다. full=1이면 진짜 전체를 스캔한다.
DEFAULT_SCAN_LIMIT = 150


def _effective_scan_limit(exchange, vol_limit, full=False):
    """상호작용 검색에 적용할 실질 스캔 상한을 반환.
    - full=True 이거나 사용자가 명시적 vol_limit(>0)을 지정하면 그대로 둔다.
    - vol_limit=0(전체) 이고 거래대금(-amount) 정렬이 신뢰 가능한 거래소(업비트/코스피)면
      상위 DEFAULT_SCAN_LIMIT 만 스캔한다. (빗썸은 정렬이 거래량 순이 아니라 제외)"""
    if vol_limit == 0 and not full and exchange in ('upbit', 'kospi'):
        return DEFAULT_SCAN_LIMIT
    return vol_limit





# 티커+실시간 시세 목록을 짧게 캐싱(초). 반복 검색 시 업비트 시세 API 왕복(~0.9s)을
# 건너뛰기 위함. 시세가 이만큼 지연될 수 있으나, 캔들 캐시(5분)·스크리너 용도상 무해하다.
_TICKERS_FRESH_TTL = 30


def _get_tickers(exchange, vol_limit):
    """거래소 티커 목록을 안정적으로 반환.

    성능: 동일 (exchange, vol_limit) 요청은 _TICKERS_FRESH_TTL초 동안 캐시된 목록을
    그대로 반환해, 매 검색마다 반복되던 실시간 시세 API 왕복을 제거한다.

    안정성: 외부 API나 DB가 간헐적으로 실패해 빈 목록이 나오면 '0/0 종목'으로 검색이
    멈추므로,
      1) 원본 조회를 최대 2회 재시도하고,
      2) 성공(비어있지 않음)하면 fresh 캐시(짧게)와 last_good 캐시(5분)에 저장,
      3) 그래도 비면 마지막 정상 목록을 폴백으로 사용한다."""
    fresh_key = f"tickers_fresh_{exchange}_{vol_limit}"
    last_good_key = f"tickers_lastgood_{exchange}_{vol_limit}"

    # 짧은 fresh 캐시 히트 시 시세 API 호출 없이 즉시 반환
    try:
        fresh = cache.get(fresh_key)
        if fresh:
            return fresh
    except Exception:
        pass

    result = []
    for attempt in range(2):
        try:
            result = _get_tickers_raw(exchange, vol_limit)
        except Exception as e:
            logger.error(f"_get_tickers_raw error ({exchange}): {e}", exc_info=True)
            result = []
        if result:
            # fresh(짧게) + last_good(5분, 장애 폴백용) 동시 저장
            try:
                cache.set(fresh_key, result, _TICKERS_FRESH_TTL)
                cache.set(last_good_key, result, 300)
            except Exception:
                pass
            return result

    # 재시도해도 비어있으면 마지막 정상 목록으로 폴백
    fallback = cache.get(last_good_key)
    if fallback:
        logger.warning(f"_get_tickers fallback to cached list for {exchange} ({len(fallback)} tickers)")
        return fallback
    return result


def _get_tickers_raw(exchange, vol_limit):
    """거래소·거래대금 조건에 맞는 티커 목록 반환 (API 직접 호출, DB는 보조)"""
    global KOSPI_NAME_MAP

    # 먼저 DB에 데이터가 있으면 DB에서 가져오기 (market_cap, amount 등 추가 정보 포함)
    try:
        from ..models import MarketData
        db_count = MarketData.objects.filter(exchange=exchange).count()
        if db_count > 0:
            qs = MarketData.objects.filter(exchange=exchange).order_by('-amount')
            if vol_limit:
                qs = qs[:vol_limit]
            result_list = list(qs.values('ticker', 'name', 'market_cap', 'amount'))
            
            # 업비트인 경우, 실시간 가격과 등락률을 단 1~2번의 API 호출(0.1초)로 일괄 갱신합니다.
            if exchange == 'upbit':
                try:
                    import requests
                    # 상장폐지 티커가 DB에 남아 있으면 시세 일괄 요청이 404로 통째로 실패하므로,
                    # 유효 마켓 목록(1시간 캐시)으로 먼저 걸러 404 자체를 방지한다.
                    valid_markets = cache.get('upbit_valid_markets')
                    if not valid_markets:
                        try:
                            ma = requests.get('https://api.upbit.com/v1/market/all', timeout=5).json()
                            if isinstance(ma, list):
                                valid_markets = set(m['market'] for m in ma if isinstance(m, dict) and 'market' in m)
                                cache.set('upbit_valid_markets', valid_markets, 3600)
                        except Exception:
                            valid_markets = None

                    tickers_only = [t['ticker'] for t in result_list]
                    if valid_markets:
                        tickers_only = [t for t in tickers_only if t in valid_markets]

                    # Upbit API는 한 번에 여러 티커(콤마 구분) 요청 가능
                    chunks = [tickers_only[i:i+100] for i in range(0, len(tickers_only), 100)]
                    change_rates = {}
                    prices = {}
                    for chunk in chunks:
                        if not chunk:
                            continue
                        try:
                            res = requests.get(f'https://api.upbit.com/v1/ticker?markets={",".join(chunk)}', timeout=5).json()
                        except Exception as ce:
                            print(f"Upbit ticker chunk error: {ce}")
                            continue
                        if isinstance(res, list):
                            for item in res:
                                change_rates[item['market']] = item.get('signed_change_rate', 0) * 100
                                prices[item['market']] = item.get('trade_price', 0)
                        else:
                            # 유효 마켓 필터 후에도 실패하면 표시용 시세만 생략(0)하고 넘어간다(느린 개별 재시도 금지)
                            print(f"Upbit ticker chunk skipped: {res}")

                    for t in result_list:
                        t['change_rate'] = change_rates.get(t['ticker'], 0)
                        t['current_price'] = prices.get(t['ticker'], 0)
                except Exception as e:
                    print(f"Error fetching real-time upbit ticker data: {e}")

            return result_list
    except Exception:
        pass  # DB 사용 불가 시 아래 API 직접 호출로 폴백

    # DB에 데이터가 없으면 원래 방식대로 API 직접 호출
    if exchange == 'kospi':
        import FinanceDataReader as fdr
        try:
            etf_df = fdr.StockListing('ETF/KR')
            
            if 'Amount' in etf_df.columns:
                etf_df = etf_df.sort_values(by='Amount', ascending=False)
                
            limit = vol_limit if vol_limit else len(etf_df)
            
            result = []
            etf_code_col = 'Symbol' if 'Symbol' in etf_df.columns else 'Code'
            for _, row in etf_df.head(limit).iterrows():
                ticker = str(row.get(etf_code_col, ''))
                name = str(row.get('Name', ''))
                cache.set(f"kospi_name_{ticker}", name, 3600*24)
                result.append({'ticker': ticker, 'name': name, 'market_cap': 0, 'amount': 0})
            
            return result
        except Exception as e:
            print(f"Error fetching KOSPI tickers: {e}")
            return []
    elif exchange == 'bithumb':
        try:
            import pybithumb
            all_tickers = pybithumb.get_tickers()
            if not all_tickers:
                return []
            
            if vol_limit:
                all_tickers = all_tickers[:vol_limit]

            result = []
            for t in all_tickers:
                # 빗썸은 별도 한글명 API가 없으므로 티커 그대로 사용하거나 하드코딩 필요
                # 편의상 티커를 이름으로 사용
                result.append({
                    'ticker': t,
                    'name': t,
                    'market_cap': 0,
                    'amount': 0,
                })
            return result
        except Exception as e:
            print(f"Error fetching Bithumb tickers: {e}")
            return []
    else:
        # 업비트 — pyupbit로 직접 가져오기
        try:
            import pyupbit, time as _t
            all_tickers = None
            for _i in range(3):  # 일시적 rate limit/네트워크 실패 대비 재시도
                all_tickers = pyupbit.get_tickers(fiat="KRW")
                if all_tickers:
                    break
                _t.sleep(0.3)
            if not all_tickers:
                return []
            
            # 이름 매핑을 위해 업비트 API 호출
            import requests
            name_dict = {}
            try:
                market_all = requests.get('https://api.upbit.com/v1/market/all', timeout=5).json()
                name_dict = {item['market']: item['korean_name'] for item in market_all if item['market'].startswith('KRW-')}
            except Exception:
                pass

            if vol_limit:
                all_tickers = all_tickers[:vol_limit]

            result = []
            for t in all_tickers:
                result.append({
                    'ticker': t,
                    'name': name_dict.get(t, t.replace("KRW-", "")),
                    'market_cap': 0,
                    'amount': 0,
                })
            return result
        except Exception as e:
            print(f"Error fetching Upbit tickers: {e}")
            return []



def coin_search(request, strategy_id):
    strategy   = get_viewable_strategy(request, strategy_id)
    conditions = list(strategy.conditions.all())

    if not conditions:
        messages.warning(request, "조건을 먼저 추가해주세요.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    exchange  = request.GET.get('exchange', 'upbit')
    # 사용자가 선택한 스캔 범위를 그대로 사용합니다. (0인 경우 전체 코인 스캔)
    try:
        vol_limit_param = request.GET.get('vol_limit')
        vol_limit = int(vol_limit_param) if vol_limit_param is not None else 0
    except (ValueError, TypeError):
        vol_limit = 0

    tf_override = request.GET.get('timeframe')
    tf_suffix = f"_{tf_override}" if tf_override else ""

    # 무조건 새로 검색하기 위해 캐시 조회를 제거하고 로딩 페이지로 바로 진입합니다.

    # 캐시 없음 → 로딩 페이지 (JS가 SSE로 진행)
    send_telegram = request.GET.get('send_telegram', '0')
    if send_telegram == '1':
        # 텔레그램은 사이트의 단일 봇/채팅방으로 전송되므로, 공용 샘플이나
        # 타인의 전략을 조회할 수 있다는 이유만으로 발송 권한까지 주지 않는다.
        get_owned_strategy(request, strategy_id)
    full_scan = '1' if request.GET.get('full') == '1' else '0'
    return render(request, 'screener/search_loading.html', {
        'strategy':  strategy,
        'exchange':  exchange,
        'vol_limit': vol_limit,
        'send_telegram': send_telegram,
        'timeframe': tf_override or '',
        'full': full_scan,
    })


@csrf_exempt
def cron_prefetch(request):
    import traceback
    from django.http import JsonResponse, HttpResponseForbidden
    from ..models import OHLCVCache
    from ..engine import get_ohlcv_with_retry, save_ohlcv_cache
    import json
    
    cron_sec = _get_cron_secret()
    is_authorized = bool(cron_sec) and request.GET.get("secret") == cron_sec

    if not is_authorized:
        return HttpResponseForbidden("Forbidden")
        
    try:
        limit = 60  # 호출당 처리량 (분봉 제거로 타임프레임이 적어 안전하게 상향)

        index_cache, _ = OHLCVCache.objects.get_or_create(
            ticker="__PREFETCH_INDEX__",
            timeframe="system",
            defaults={"data": {"start": 0}}
        )

        if isinstance(index_cache.data, str):
            index_cache.data = json.loads(index_cache.data)

        start_idx = index_cache.data.get("start", 0)

        # 작업 목록(티커×타임프레임)은 자주 바뀌지 않으므로 10분 캐시.
        # 매 호출마다 3개 거래소 티커 전체를 다시 불러오던 비용(수십 초)을 제거한다.
        tasks = cache.get('prefetch_tasks')
        if not tasks:
            active_timeframes = {"day"}
            from ..models import Strategy
            for s in Strategy.objects.all():
                for c in s.conditions.all():
                    active_timeframes.add(c.timeframe)

            tasks = []
            for ex in ['upbit', 'bithumb', 'kospi']:
                tickers_info = _get_tickers(ex, 0)
                for t_info in tickers_info:
                    for tf in active_timeframes:
                        if ex == 'kospi' and tf not in ['day', 'week', 'month']:
                            continue
                        tasks.append({"exchange": ex, "ticker": t_info["ticker"], "timeframe": tf})
            cache.set('prefetch_tasks', tasks, 600)

        total_tasks = len(tasks)
        
        if start_idx >= total_tasks:
            start_idx = 0
            
        end_idx = min(start_idx + limit, total_tasks)
        batch_tasks = tasks[start_idx:end_idx]
        
        success_count = 0
        error_count = 0
        
        import concurrent.futures

        def fetch_only(task):
            # 워커는 외부 조회만 수행하고 SQLite 저장은 요청 스레드에서 처리한다.
            try:
                df = get_ohlcv_with_retry(
                    task["ticker"], task["timeframe"],
                    count=200, exchange=task["exchange"], persist_db=False,
                )
                return task, df
            except Exception:
                return task, None
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_only, t) for t in batch_tasks]
            for future in concurrent.futures.as_completed(futures):
                task, df = future.result()
                if df is None or len(df) == 0:
                    error_count += 1
                    continue
                try:
                    save_ohlcv_cache(task["ticker"], task["timeframe"], df)
                    success_count += 1
                except Exception:
                    error_count += 1
                    
        next_start = end_idx if end_idx < total_tasks else 0
        index_cache.data = {"start": next_start}
        index_cache.save()
        
        return JsonResponse({
            "ok": True,
            "message": f"Prefetched {success_count}/{len(batch_tasks)} items",
            "start_idx": start_idx,
            "next_start": next_start,
            "total": total_tasks
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e), "trace": traceback.format_exc()})

@csrf_exempt
def trigger_migrate(request):
    cron_sec = _get_cron_secret()
    if not cron_sec or request.GET.get('secret') != cron_sec:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("권한이 없습니다.")
        
    from django.core.management import call_command
    import io
    out = io.StringIO()
    try:
        call_command('migrate', interactive=False, stdout=out)
        return JsonResponse({'ok': True, 'log': out.getvalue()})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})

@csrf_exempt
def trigger_debug(request):
    cron_sec = _get_cron_secret()
    if not cron_sec or request.GET.get('secret') != cron_sec:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("권한이 없습니다.")
        
    try:
        from ..models import OHLCVCache
        count = OHLCVCache.objects.count()
        timeframes = list(OHLCVCache.objects.values_list('timeframe', flat=True).distinct())
        return JsonResponse({'ok': True, 'count': count, 'timeframes': timeframes})
    except Exception as e:
        import traceback
        return JsonResponse({'ok': False, 'error': str(e), 'trace': traceback.format_exc()})


def _bulk_prefetch_ohlcv(tickers_data, conditions, exchange=None):
    """OHLCVCache DB에서 필요한 OHLCV 데이터를 일괄 조회해 메모리 캐시에 적재.

    (A) 인프로세스 파싱 캐시 적용:
      - 먼저 (id, ticker, timeframe, updated_at)만 가볍게 조회한다(무거운 data 컬럼 제외).
      - 인프로세스 캐시의 updated_at과 비교해 '변경된 항목의 data'만 다시 읽어 파싱한다.
      - 대부분의 반복 검색은 크롤러 갱신(5분) 전이라 재파싱이 0에 수렴한다.
    DB에 아예 없는 항목은 병렬 HTTP로 즉시 채운다(콜드 스타트).
    exchange 명시 시 라이브 재조회의 거래소 라우팅이 정확해짐(빗썸)."""
    try:
        import time as _time
        from ..models import OHLCVCache
        from django.core.cache import cache
        import pandas as pd
        import concurrent.futures
        from ..engine import (
            get_ohlcv_with_retry,
            get_max_required_len,
            max_cache_age,
            save_ohlcv_cache,
        )
        from django.utils import timezone

        req_count = get_max_required_len(conditions)
        active_timeframes = set(c.timeframe for c in conditions)
        tickers = [t['ticker'] if isinstance(t, dict) else t for t in tickers_data]

        # 1) 가벼운 메타 조회: 큰 data 컬럼을 빼고 (id, ticker, timeframe, updated_at)만 읽는다.
        _tm0 = _time.perf_counter()
        meta_qs = OHLCVCache.objects.filter(
            ticker__in=tickers, timeframe__in=active_timeframes
        ).values('id', 'ticker', 'timeframe', 'updated_at')

        present_keys = set()
        fresh_keys = set()
        stale_keys = set()
        reparse_ids = []
        now = timezone.now()
        for m in meta_qs:
            key = (m['ticker'], m['timeframe'])
            present_keys.add(key)

            # DB 행이 존재하더라도 해당 캔들 주기보다 오래됐다면 스캔 캐시에
            # 다시 넣지 않는다. 메모리/파싱 캐시도 제거한 뒤 아래 라이브 갱신
            # 대상으로 넘겨, 크롤러 장애가 오래된 신호로 위장되지 않게 한다.
            age_seconds = (now - m['updated_at']).total_seconds()
            if age_seconds >= max_cache_age(m['timeframe']):
                stale_keys.add(key)
                with _PARSED_OHLCV_LOCK:
                    _PARSED_OHLCV.pop(key, None)
                cache.delete(f"ohlcv_{key[0]}_{key[1]}_{req_count}")
                continue

            fresh_keys.add(key)
            with _PARSED_OHLCV_LOCK:
                cached = _PARSED_OHLCV.get(key)
            if cached is None or cached[0] != m['updated_at']:
                reparse_ids.append(m['id'])
        t_meta = _time.perf_counter() - _tm0

        # 2) 변경된(또는 처음 보는) 항목만 무거운 data를 읽어 파싱한다.
        _tp0 = _time.perf_counter()
        if reparse_ids:
            for obj in OHLCVCache.objects.filter(id__in=reparse_ids):
                data_dict = obj.data
                try:
                    df = pd.DataFrame(
                        data_dict['data'],
                        index=pd.to_datetime(data_dict['index'], unit='ms'),
                        columns=data_dict['columns'],
                    )
                    df.index.name = None
                except Exception:
                    continue
                if len(df) == 0:
                    continue
                key = (obj.ticker, obj.timeframe)
                with _PARSED_OHLCV_LOCK:
                    # 상한 초과 시 단순 방어적으로 비운다(장수 프로세스 메모리 보호).
                    if len(_PARSED_OHLCV) >= _PARSED_OHLCV_MAX and key not in _PARSED_OHLCV:
                        _PARSED_OHLCV.clear()
                    _PARSED_OHLCV[key] = (obj.updated_at, df)
        t_parse = _time.perf_counter() - _tp0

        # 3) 요청 타임프레임에 대해 파싱된 프레임을 요청 단위 LocMemCache에 적재.
        #    check_strategy가 지표 컬럼을 in-place로 추가하므로 캐시 원본 보호를 위해 copy() 전달.
        for key in fresh_keys:
            with _PARSED_OHLCV_LOCK:
                entry = _PARSED_OHLCV.get(key)
            if entry is None:
                continue
            df = entry[1]
            cache_key = f"ohlcv_{key[0]}_{key[1]}_{req_count}"
            cache.set(
                cache_key,
                df.tail(req_count).copy(),
                min(180, max_cache_age(key[1])),
            )

        # 4) DB에 없거나 오래된 항목은 라이브로 채운다.
        #    개별 sleep 대신 engine._throttle() 전역 속도 제한에 위임(워커를 늘려도 ≈9req/s로 안전).
        refresh_tasks = [
            (t, tf) for t in tickers for tf in active_timeframes
            if (t, tf) not in fresh_keys
        ]
        _tf0 = _time.perf_counter()
        if refresh_tasks:
            def _fetch_one(item):
                t, tf = item
                # 오래된 동일 count 메모리 캐시가 라이브 갱신을 가로막지 않게 보장.
                cache.delete(f"ohlcv_{t}_{tf}_{req_count}")
                df = get_ohlcv_with_retry(
                    t,
                    tf,
                    count=req_count,
                    exchange=exchange,
                    persist_db=False,
                )
                return t, tf, df

            with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
                fetched = list(executor.map(_fetch_one, refresh_tasks))

            # SQLite 쓰기는 워커 밖에서 순차 실행해 lock 경쟁을 줄인다.
            for t, tf, df in fetched:
                if df is not None and len(df) > 0:
                    save_ohlcv_cache(t, tf, df)
        t_live = _time.perf_counter() - _tf0

        # [측정] 프리페치 내부 분해: 메타조회 / 재파싱(변경분) / 누락 라이브조회.
        #   코스피에서 t_live가 크면 '캐시 워밍'이 답, t_parse가 크면 파싱/사전계산(D)이 답.
        print(
            f"[PREFETCH_TIMING] ex={exchange} present={len(present_keys)} "
            f"fresh={len(fresh_keys)} stale={len(stale_keys)} "
            f"reparsed={len(reparse_ids)} refresh_live={len(refresh_tasks)} | "
            f"meta={t_meta:.2f}s parse={t_parse:.2f}s live={t_live:.2f}s"
        )
    except Exception as e:
        print(f"Bulk cache prefetch error: {e}")


def coin_search_stream(request, strategy_id):
    """SSE: 검색 진행률 + 최종 결과 스트리밍"""
    from django.http import StreamingHttpResponse

    strategy   = get_viewable_strategy(request, strategy_id)
    conditions = list(strategy.conditions.all())
    exchange   = request.GET.get('exchange', 'upbit')
    try:
        vol_limit_param = request.GET.get('vol_limit')
        vol_limit = int(vol_limit_param) if vol_limit_param is not None else 0
    except (ValueError, TypeError):
        vol_limit = 0

    tf_override = request.GET.get('timeframe')
    if tf_override:
        for c in conditions:
            c.timeframe = tf_override

    send_telegram = request.GET.get('send_telegram') == '1'
    if send_telegram:
        # SSE URL을 직접 호출해 로딩 페이지의 검사를 우회하는 경우도 차단한다.
        get_owned_strategy(request, strategy_id)
    full_scan = request.GET.get('full') == '1'

    def event_stream():
        import time
        start_time = time.time()

        if not conditions:
            yield "data: " + json.dumps({"type": "error", "msg": "조건이 없습니다."}) + "\n\n"
            return

        # (B) vol_limit=0(전체)이라도 full=1이 아니면 거래대금 상위 N만 스캔(업비트/코스피)
        scan_limit = _effective_scan_limit(exchange, vol_limit, full_scan)
        _t_tickers0 = time.perf_counter()
        tickers_data = _get_tickers(exchange, scan_limit)
        t_tickers = time.perf_counter() - _t_tickers0
        total   = len(tickers_data)

        # 티커를 못 불러오면(외부 API/DB 일시 장애) 조용히 멈추지 않고 명확히 알림
        if total == 0:
            yield "data: " + json.dumps({
                "type": "error",
                "msg": "종목 목록을 불러오지 못했습니다. 잠시 후 다시 시도해주세요."
            }) + "\n\n"
            return

        results = []
        done    = 0
        error_occurred = False

        def process_ticker(t_data):
            ticker = t_data['ticker']
            name = t_data['name']
            market_cap = t_data.get('market_cap') or 0
            amount = t_data.get('amount') or 0
            fast_change_rate = t_data.get('change_rate')
            fast_price = t_data.get('current_price')

            try:
                is_match, details, price, volume, change_rate, status = check_strategy(
                    ticker, conditions,
                    current_price=fast_price,
                    current_change_rate=fast_change_rate,
                    exchange=exchange,
                    persist_db=False,
                )
                if price is None:
                    return "API_ERROR"
                if is_match:
                    unique_details = list(dict.fromkeys(details))
                    return {
                        'symbol':         ticker,
                        'name':           name,
                        'market_cap':     market_cap,
                        'market_cap_display': f"{market_cap / 100_000_000:.1f}억" if market_cap else "-",
                        'amount':         amount,
                        'amount_display': f"{amount / 100_000_000:.1f}억" if amount else "-",
                        'price':          price,
                        'change_rate':    change_rate,
                        'details':        ", ".join(unique_details),
                        'volume':         volume,
                        'volume_display': f"{volume:.0f}" if volume else "0",
                        'status':         status,
                    }
            except Exception:
                pass
            return None

        _t_prefetch0 = time.perf_counter()
        _bulk_prefetch_ohlcv(tickers_data, conditions, exchange=exchange)
        t_prefetch = time.perf_counter() - _t_prefetch0

        # 프리페치 후 대부분 캐시 히트이고, 잔여 라이브 조회는 engine._throttle()로 전역
        # 속도 제한되므로 워커를 늘려도 안전하다. (병렬 처리로 스캔 지연 최소화)
        _t_scan0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(process_ticker, t): t for t in tickers_data}
            last_sent_pct = -1
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                done  += 1
                if result == "API_ERROR":
                    error_occurred = True
                elif result:
                    results.append(result)

                pct = int(done / total * 100) if total else 100
                if pct >= last_sent_pct + 2 or done == total:
                    last_sent_pct = pct
                    # 마지막 매칭 코인 이름 전송 (로딩 화면 표시용)
                    last_match = (results[-1].get('name') or results[-1]['symbol']) if results else None
                    yield "data: " + json.dumps({
                        "type":    "progress",
                        "done":    done,
                        "total":   total,
                        "pct":     pct,
                        "matched": len(results),
                        "last_match": last_match,
                    }) + "\n\n"
        t_scan = time.perf_counter() - _t_scan0

        # [측정] 구간별 소요시간 로그: 4초가 어디서 쓰이는지 확정용 (journalctl에서 확인)
        print(
            f"[SEARCH_TIMING] ex={exchange} tickers={total} matched={len(results)} "
            f"api_err={error_occurred} | get_tickers={t_tickers:.2f}s "
            f"prefetch(parse)={t_prefetch:.2f}s scan(calc)={t_scan:.2f}s "
            f"total={time.time() - start_time:.2f}s"
        )

        results.sort(key=lambda x: x.get('volume', 0), reverse=True)
        last_updated = timezone.now()
        elapsed_seconds = round(time.time() - start_time, 2)

        cache_key = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}"
        if tf_override:
            cache_key += f"_{tf_override}"

        # 프로세스 간 LocMemCache가 공유되지 않는 환경을 고려해,
        # 검색 결과를 DB(OHLCVCache)를 활용하여 임시 저장합니다. (무한 리다이렉트 방지)
        from ..engine import save_cache_payload
        try:
            save_cache_payload(
                cache_key,
                "RESULT",
                {
                    'results': results,
                    'rate_limit_warning': error_occurred,
                    'last_updated': last_updated.isoformat(),
                    'elapsed_time': elapsed_seconds,
                }
            )
        except Exception as e:
            print(f"결과 저장 실패: {e}")

        # 만약 자동 반복 스캔에서 텔레그램 전송이 활성화되었고, 조회된 건이 있으면 즉시 발송
        if send_telegram and results and tg.is_configured():
            try:
                import datetime as _datetime
                duplicate_cutoff = timezone.now() - _datetime.timedelta(hours=12)
                recently_notified = set(
                    AlertHistory.objects.filter(
                        strategy=strategy,
                        is_notified=True,
                        created_at__gte=duplicate_cutoff,
                    ).values_list('symbol', flat=True)
                )
                notify_results = [
                    r for r in results if r['symbol'] not in recently_notified
                ]
                AlertHistory.objects.bulk_create([
                    AlertHistory(
                        strategy=strategy,
                        symbol=r['symbol'],
                        price=r['price'],
                        volume=r['volume'],
                        details=r['details'],
                        status=r['status'],
                        is_notified=r['symbol'] not in recently_notified,
                    )
                    for r in results
                ])
                if notify_results:
                    tg.send_alert(
                        strategy.name,
                        notify_results,
                        strategy_id=strategy.id,
                        exchange=exchange,
                    )
            except Exception as e:
                print(f"자동 반복 스캔 중 텔레그램 발송 실패: {e}")

        yield "data: " + json.dumps({
            "type":     "done",
            "redirect": f"/strategy/{strategy_id}/results/?exchange={exchange}&vol_limit={vol_limit}{f'&timeframe={tf_override}' if tf_override else ''}",
        }) + "\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def coin_search_results(request, strategy_id):
    """SSE 완료 후 결과 페이지 렌더 (캐시에서 읽음)"""
    strategy   = get_viewable_strategy(request, strategy_id)
    exchange   = request.GET.get('exchange', 'upbit')
    vol_limit  = int(request.GET.get('vol_limit', 0) or 0)
    tf_override = request.GET.get('timeframe')
    tf_suffix = f"_{tf_override}" if tf_override else ""
    
    cache_key  = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}{tf_suffix}"
    
    # DB(OHLCVCache)에서 결과 읽어오기
    from ..models import OHLCVCache
    import dateutil.parser
    try:
        obj = OHLCVCache.objects.get(ticker=cache_key, timeframe="RESULT")
        cached_data = obj.data
        if 'last_updated' in cached_data and isinstance(cached_data['last_updated'], str):
            cached_data['last_updated'] = dateutil.parser.isoparse(cached_data['last_updated'])
    except Exception:
        cached_data = None

    if not cached_data:
        return redirect('coin_search', strategy_id=strategy_id)

    return render(request, 'screener/coin_list.html', {
        'results':            cached_data['results'],
        'strategy':           strategy,
        'rate_limit_warning': cached_data['rate_limit_warning'],
        'is_cached':          False,
        'last_updated':       cached_data.get('last_updated'),
        'elapsed_time':       cached_data.get('elapsed_time'),
    })


