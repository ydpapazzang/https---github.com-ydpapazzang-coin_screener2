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

logger = logging.getLogger(__name__)

def _get_cron_secret():
    return os.environ.get('CRON_SECRET', '')





def _get_tickers(exchange, vol_limit):
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
            return list(qs.values('ticker', 'name', 'market_cap', 'amount'))
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
            import pyupbit
            all_tickers = pyupbit.get_tickers(fiat="KRW")
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
    strategy   = get_object_or_404(Strategy, id=strategy_id)
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
    return render(request, 'screener/search_loading.html', {
        'strategy':  strategy,
        'exchange':  exchange,
        'vol_limit': vol_limit,
        'send_telegram': send_telegram,
        'timeframe': tf_override or '',
    })


@csrf_exempt
def cron_prefetch(request):
    import traceback
    from django.http import JsonResponse, HttpResponseForbidden
    from ..models import OHLCVCache
    from ..engine import get_ohlcv_with_retry
    import json
    
    is_cron = request.headers.get("x-vercel-cron") == "1"
    cron_sec = _get_cron_secret()
    is_debug = bool(cron_sec) and request.GET.get("secret") == cron_sec
    
    if not is_cron and not is_debug:
        return HttpResponseForbidden("Forbidden")
        
    try:
        limit = 25
        
        index_cache, _ = OHLCVCache.objects.get_or_create(
            ticker="__PREFETCH_INDEX__", 
            timeframe="system",
            defaults={"data": {"start": 0}}
        )
        
        if isinstance(index_cache.data, str):
            index_cache.data = json.loads(index_cache.data)
            
        start_idx = index_cache.data.get("start", 0)

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
                
        total_tasks = len(tasks)
        
        if start_idx >= total_tasks:
            start_idx = 0
            
        end_idx = min(start_idx + limit, total_tasks)
        batch_tasks = tasks[start_idx:end_idx]
        
        success_count = 0
        error_count = 0
        
        import concurrent.futures
        import pyupbit
        import pandas as pd

        def _resample(df, rule):
            if df is None or df.empty: return df
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            res = df.resample(rule).agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            return res.dropna()

        def fetch_and_save(task):
            ex = task["exchange"]
            ticker = task["ticker"]
            tf = task["timeframe"]
            df = None
            try:
                if ex == 'upbit':
                    df = pyupbit.get_ohlcv(ticker, interval=tf, count=200)
                elif ex == 'bithumb':
                    import pybithumb
                    bithumb_tf_map = {'minute15': 'minute5', 'minute30': 'minute30', 'minute60': 'hour', 'minute240': 'hour', 'day': 'day', 'week': 'day', 'month': 'day'}
                    btf = bithumb_tf_map.get(tf, 'day')
                    df = pybithumb.get_ohlcv(ticker, interval=btf)
                    if df is not None and not df.empty:
                        df.index.name = None
                        if tf == 'minute15': df = _resample(df, '15min')
                        elif tf == 'minute240': df = _resample(df, '4h')
                        elif tf == 'week': df = _resample(df, 'W-MON')
                        elif tf == 'month': df = _resample(df, 'ME')
                        df = df.tail(200)
                elif ex == 'kospi':
                    import FinanceDataReader as fdr
                    df = fdr.DataReader(ticker)
                    if df is not None and not df.empty:
                        df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
                        if 'Change' in df.columns:
                            df.drop(columns=['Change'], inplace=True)
                        if tf == 'week': df = _resample(df, 'W-FRI')
                        elif tf == 'month': df = _resample(df, 'ME')
                        df = df.tail(200)

                if df is not None and not df.empty:
                    json_data = json.loads(df.tojson(orient="split"))
                    OHLCVCache.objects.update_or_create(
                        ticker=ticker,
                        timeframe=tf,
                        defaults={"data": json_data}
                    )
                    return True
            except Exception as e:
                pass
            return False
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(fetch_and_save, t) for t in batch_tasks]
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    success_count += 1
                else:
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


def _bulk_prefetch_ohlcv(tickers_data, conditions):
    """OHLCVCache DB에서 필요한 OHLCV 데이터를 일괄 조회해 메모리 캐시에 적재."""
    try:
        from ..models import OHLCVCache
        from django.core.cache import cache
        import pandas as pd
        import datetime

        active_timeframes = set(c.timeframe for c in conditions)
        active_timeframes.add('day')
        tickers = [t['ticker'] if isinstance(t, dict) else t for t in tickers_data]

        cached_qs = OHLCVCache.objects.filter(ticker__in=tickers, timeframe__in=active_timeframes)
        now = datetime.datetime.now(datetime.timezone.utc)

        for obj in cached_qs:
            if (now - obj.updated_at).total_seconds() < 604800:
                data_dict = obj.data
                try:
                    df = pd.DataFrame(
                        data_dict['data'],
                        index=pd.to_datetime(data_dict['index'], unit='ms'),
                        columns=data_dict['columns'],
                    )
                    df.index.name = None
                    cache_key = f"ohlcv_{obj.ticker}_{obj.timeframe}_200"
                    cache.set(cache_key, df.tail(200), 180)
                except Exception:
                    pass
    except Exception as e:
        print(f"Bulk cache prefetch error: {e}")


def coin_search_stream(request, strategy_id):
    """SSE: 검색 진행률 + 최종 결과 스트리밍"""
    from django.http import StreamingHttpResponse

    strategy   = get_object_or_404(Strategy, id=strategy_id)
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

    def event_stream():
        if not conditions:
            yield "data: " + json.dumps({"type": "error", "msg": "조건이 없습니다."}) + "\n\n"
            return

        tickers_data = _get_tickers(exchange, vol_limit)
        total   = len(tickers_data)
        results = []
        done    = 0
        error_occurred = False

        def process_ticker(t_data):
            ticker = t_data['ticker']
            name = t_data['name']
            market_cap = t_data.get('market_cap') or 0
            amount = t_data.get('amount') or 0

            try:
                is_match, details, price, volume, change_rate, status = check_strategy(ticker, conditions)
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

        _bulk_prefetch_ohlcv(tickers_data, conditions)

        # 스레드 개수를 10개로 조절하여 업비트 API 호출의 순간 폭주(Burst)를 완화하고 Rate Limit를 방어합니다.
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
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

        results.sort(key=lambda x: x.get('volume', 0), reverse=True)
        last_updated = timezone.now()
        cache_key = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}"
        if tf_override:
            cache_key += f"_{tf_override}"

        # Vercel 서버리스 환경에서는 컨테이너 간 LocMemCache가 공유되지 않으므로, 
        # 검색 결과를 DB(OHLCVCache)를 활용하여 임시 저장합니다. (무한 리다이렉트 방지)
        from ..models import OHLCVCache
        try:
            OHLCVCache.objects.update_or_create(
                ticker=cache_key,
                timeframe="RESULT",
                defaults={
                    "data": {
                        'results': results,
                        'rate_limit_warning': error_occurred,
                        'last_updated': last_updated.isoformat()
                    }
                }
            )
        except Exception as e:
            print(f"결과 저장 실패: {e}")

        # 만약 자동 반복 스캔에서 텔레그램 전송이 활성화되었고, 조회된 건이 있으면 즉시 발송
        if send_telegram and results and tg.is_configured():
            try:
                for r in results:
                    AlertHistory.objects.create(
                        strategy=strategy,
                        symbol=r['symbol'],
                        price=r['price'],
                        volume=r['volume'],
                        details=r['details'],
                        status=r['status'],
                        is_notified=True
                    )
                tg.send_alert(strategy.name, results, strategy_id=strategy.id, exchange=exchange)
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
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    exchange   = request.GET.get('exchange', 'upbit')
    vol_limit  = int(request.GET.get('vol_limit', 0) or 0)
    tf_override = request.GET.get('timeframe')
    tf_suffix = f"_{tf_override}" if tf_override else ""
    
    cache_key  = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}{tf_suffix}"
    
    # DB(OHLCVCache)에서 결과 읽어오기 (Vercel 환경 지원)
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
    })


