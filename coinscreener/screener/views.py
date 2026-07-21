from django.shortcuts import render, redirect, get_object_or_404
from .models import Strategy, Condition, AlertSetting, AlertHistory
from .engine import check_strategy
import pyupbit
import concurrent.futures
from django.utils import timezone
from django.contrib import messages
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
import json


# ──────────────────────────────────────────
# 1. 전략 리스트
# ──────────────────────────────────────────

def clear_strategy_cache(strategy_id):
    exchanges = ['upbit', 'bithumb']
    vol_limits = [0, 30, 50, 80, 100, 200]
    cache.delete(f"strategy_results_{strategy_id}")
    for ex in exchanges:
        for vol in vol_limits:
            cache.delete(f"strategy_results_{strategy_id}_{ex}_{vol}")


def strategy_list(request):
    strategies = Strategy.objects.all().order_by('-created_at')
    return render(request, 'screener/strategy_list.html', {'strategies': strategies})


def strategy_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, "전략 이름을 입력해주세요.")
            return redirect('strategy_list')
        strategy = Strategy.objects.create(name=name)
        return redirect('strategy_detail', strategy_id=strategy.id)
    return redirect('strategy_list')


def strategy_delete(request):
    if request.method == 'POST':
        strategy_ids = request.POST.getlist('strategy_ids')
        for s_id in strategy_ids:
            clear_strategy_cache(s_id)
        Strategy.objects.filter(id__in=strategy_ids).delete()
    return redirect('strategy_list')


# ──────────────────────────────────────────
# 2. 전략 상세 / 조건 관리
# ──────────────────────────────────────────

def strategy_detail(request, strategy_id):
    strategies = Strategy.objects.all().order_by('-created_at')
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = strategy.conditions.all()
    # 최근 100건의 알림 이력 조회
    histories  = strategy.histories.all().order_by('-created_at')[:100]
    return render(request, 'screener/strategy_trading.html', {
        'strategies': strategies,
        'strategy':   strategy,
        'conditions': conditions,
        'histories':  histories,
    })


def condition_add(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)

    if request.method != 'POST':
        return redirect('strategy_detail', strategy_id=strategy_id)

    cond_type = request.POST.get('cond_type', '').upper()
    timeframe = request.POST.get('timeframe', 'day')
    operator  = request.POST.get('operator', 'gte')
    bb_std    = None

    try:
        offset = int(request.POST.get('offset', 0))
    except ValueError:
        messages.error(request, "n봉 전 값이 올바르지 않습니다.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    if offset < 0:
        messages.error(request, "n봉 전은 0 이상의 숫자여야 합니다.")
        return redirect('strategy_detail', strategy_id=strategy_id)

    if cond_type == 'MA':
        ma_type_a    = request.POST.get('ma_type_a', 'MA')
        ma_type_b    = request.POST.get('ma_type_b', 'MA')
        price_a_type = request.POST.get('ma_price_a_type', 'MA')

        valid_ma = ('MA', 'EMA', 'WMA')
        if ma_type_a not in valid_ma: ma_type_a = 'MA'
        if ma_type_b not in valid_ma: ma_type_b = 'MA'

        try:
            ma_a_val = int(request.POST.get('ma_price_a_val', 5))
            ma_b_val = int(request.POST.get('ma_price_b_val', 20))
        except ValueError:
            messages.error(request, "이동평균 기간 값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        if price_a_type == 'CLOSE':
            left_indicator, left_param = 'CLOSE', 0
        else:
            if ma_a_val < 1:
                messages.error(request, "이동평균A 기간은 1 이상이어야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            left_indicator, left_param = ma_type_a, ma_a_val

        if ma_b_val < 1:
            messages.error(request, "이동평균B 기간은 1 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)
        right_indicator, right_param = ma_type_b, ma_b_val

    elif cond_type == 'RSI':
        try:
            rsi_period    = int(request.POST.get('rsi_period', 14))
            rsi_threshold = int(request.POST.get('rsi_threshold', 30))
        except ValueError:
            messages.error(request, "RSI 기간 또는 기준값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        if operator == 'btw':
            try:
                rsi_threshold_max = int(request.POST.get('rsi_threshold_max', 70))
            except ValueError:
                messages.error(request, "RSI 최대 기준값이 올바르지 않습니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            if not (0 <= rsi_threshold_max <= 100):
                messages.error(request, "RSI 최대 기준값은 0에서 100 사이여야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            if rsi_threshold >= rsi_threshold_max:
                messages.error(request, "최소값은 최대값보다 작아야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            bb_std = float(rsi_threshold_max)

        if rsi_period < 1:
            messages.error(request, "RSI 기간은 1 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)
        if not (0 <= rsi_threshold <= 100):
            messages.error(request, "RSI 기준값은 0에서 100 사이여야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        left_indicator, left_param   = 'RSI', rsi_period
        right_indicator, right_param = 'VAL', rsi_threshold

    elif cond_type == 'BB':
        try:
            bb_period = int(request.POST.get('bb_period', 20))
            bb_target = request.POST.get('bb_target', 'BB_UPPER')
        except ValueError:
            messages.error(request, "볼린저밴드 기간 값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        valid_bb = ('BB_UPPER', 'BB_MIDDLE', 'BB_LOWER')
        if bb_target not in valid_bb: bb_target = 'BB_UPPER'

        if bb_period < 1:
            messages.error(request, "볼린저밴드 기간은 1 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        left_indicator, left_param   = 'CLOSE', 0
        right_indicator, right_param = bb_target, bb_period
        bb_std                       = 2.0

    elif cond_type == 'HA':
        ha_pattern = request.POST.get('ha_pattern', 'HA_BULL')
        valid_ha = ('HA_BULL', 'HA_BEAR', 'HA_BULL_N', 'HA_BEAR_N', 'HA_NO_LOWER', 'HA_NO_UPPER')
        if ha_pattern not in valid_ha:
            messages.error(request, "올바르지 않은 하이킨아시 패턴입니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        try:
            ha_n = int(request.POST.get('ha_n', 3))
        except ValueError:
            ha_n = 3
        ha_n = max(1, min(ha_n, 20))

        # 하이킨아시는 left_indicator에 패턴, operator='is', right는 더미
        left_indicator,  left_param   = ha_pattern, ha_n
        operator                      = 'is'
        right_indicator, right_param  = 'VAL', 0

    elif cond_type == 'IC':
        ic_comparison = request.POST.get('ic_comparison', 'TENKAN_KIJUN')
        valid_ic = ('TENKAN_KIJUN', 'CLOSE_SPAN_A', 'CLOSE_SPAN_B', 'SPAN_A_SPAN_B', 'CHIKOU_CLOSE', 'CLOSE_KIJUN', 'CLOSE_CLOUD')
        if ic_comparison not in valid_ic:
            messages.error(request, "올바르지 않은 일목균형표 비교 유형입니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        if ic_comparison == 'TENKAN_KIJUN':
            left_indicator, left_param = 'IC_TENKAN', 9
            right_indicator, right_param = 'IC_KIJUN', 26
        elif ic_comparison == 'CLOSE_SPAN_A':
            left_indicator, left_param = 'CLOSE', 0
            right_indicator, right_param = 'IC_SPAN_A', 26
        elif ic_comparison == 'CLOSE_SPAN_B':
            left_indicator, left_param = 'CLOSE', 0
            right_indicator, right_param = 'IC_SPAN_B', 26
        elif ic_comparison == 'SPAN_A_SPAN_B':
            left_indicator, left_param = 'IC_SPAN_A', 26
            right_indicator, right_param = 'IC_SPAN_B', 26
        elif ic_comparison == 'CHIKOU_CLOSE':
            left_indicator, left_param = 'IC_CHIKOU', 0
            right_indicator, right_param = 'IC_CHIKOU_REF', 26
        elif ic_comparison == 'CLOSE_KIJUN':
            left_indicator, left_param = 'CLOSE', 0
            right_indicator, right_param = 'IC_KIJUN', 26
        elif ic_comparison == 'CLOSE_CLOUD':
            left_indicator, left_param = 'CLOSE', 0
            if operator in ('cross_down', 'lte', 'lt'):
                right_indicator = 'IC_CLOUD_BOTTOM'
            else:
                right_indicator = 'IC_CLOUD_TOP'
            right_param = 26

    elif cond_type == 'VOLUME':
        volume_target = request.POST.get('volume_target', 'prev')
        try:
            volume_pct = int(request.POST.get('volume_pct', 150))
        except ValueError:
            volume_pct = 150

        if volume_pct < 1:
            messages.error(request, "기준 비율은 1% 이상이어야 합니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

        bb_std = volume_pct / 100.0  # multiplier
        left_indicator = 'VOLUME'
        left_param = 0

        if operator == 'btw':
            try:
                volume_pct_max = int(request.POST.get('volume_pct_max', 300))
            except ValueError:
                messages.error(request, "최대 기준 비율이 올바르지 않습니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)

            if volume_pct_max <= volume_pct:
                messages.error(request, "최대 비율은 최소 비율보다 커야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            left_param = volume_pct_max

        if volume_target == 'prev':
            right_indicator = 'VOLUME_PREV'
            right_param = 1
        elif volume_target == 'ma':
            try:
                volume_period = int(request.POST.get('volume_period', 5))
            except ValueError:
                volume_period = 5
            if volume_period < 1:
                messages.error(request, "평균 거래량 기간은 1 이상이어야 합니다.")
                return redirect('strategy_detail', strategy_id=strategy_id)
            right_indicator = 'VOLUME_MA'
            right_param = volume_period
        else:
            messages.error(request, "올바르지 않은 거래량 비교 유형입니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)

    elif cond_type == 'CHANGE_RATE':
        try:
            cr_threshold = float(request.POST.get('cr_threshold', 0))
        except ValueError:
            messages.error(request, "등락률 기준값이 올바르지 않습니다.")
            return redirect('strategy_detail', strategy_id=strategy_id)
        left_indicator, left_param   = 'CHANGE_RATE', 0
        right_indicator, right_param = 'VAL', round(cr_threshold)

    else:
        messages.error(request, f"알 수 없는 조건 유형입니다: {cond_type}")
        return redirect('strategy_detail', strategy_id=strategy_id)

    Condition.objects.create(
        strategy=strategy,
        timeframe=timeframe,
        offset=offset,
        left_indicator=left_indicator,
        left_param=left_param,
        operator=operator,
        right_indicator=right_indicator,
        right_param=right_param,
        bb_std=bb_std,
    )
    clear_strategy_cache(strategy_id)
    return redirect('strategy_detail', strategy_id=strategy_id)


def condition_delete(request, strategy_id, condition_id):
    if request.method == 'POST':
        condition = get_object_or_404(Condition, id=condition_id)
        condition.delete()
        clear_strategy_cache(strategy_id)
    return redirect('strategy_detail', strategy_id=strategy_id)


# ──────────────────────────────────────────
# 3. 코인 검색 — SSE 스트리밍 버전
# ──────────────────────────────────────────

KOSPI_NAME_MAP = {}

def _get_tickers(exchange, vol_limit):
    """거래소·거래대금 조건에 맞는 티커 목록 반환 (API 직접 호출, DB는 보조)"""
    global KOSPI_NAME_MAP

    # 먼저 DB에 데이터가 있으면 DB에서 가져오기 (market_cap, amount 등 추가 정보 포함)
    try:
        from .models import MarketData
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
                KOSPI_NAME_MAP[ticker] = name
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
    from .models import OHLCVCache
    from .engine import get_ohlcv_with_retry
    import json
    
    is_cron = request.headers.get("x-vercel-cron") == "1"
    is_debug = request.GET.get("secret") == "wonii_cron_debug"
    
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
        from .models import Strategy
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
                    json_data = json.loads(df.to_json(orient="split"))
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
    if request.GET.get('secret') != 'wonii_cron_debug':
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("권한이 없습니다.")
        
    from django.core.management import call_command
    out = io.StringIO()
    try:
        call_command('migrate', interactive=False, stdout=out)
        return JsonResponse({'ok': True, 'log': out.getvalue()})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})

@csrf_exempt
def trigger_debug(request):
    if request.GET.get('secret') != 'wonii_cron_debug':
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("권한이 없습니다.")
        
    try:
        from .models import OHLCVCache
        count = OHLCVCache.objects.count()
        timeframes = list(OHLCVCache.objects.values_list('timeframe', flat=True).distinct())
        return JsonResponse({'ok': True, 'count': count, 'timeframes': timeframes})
    except Exception as e:
        import traceback
        return JsonResponse({'ok': False, 'error': str(e), 'trace': traceback.format_exc()})


def _bulk_prefetch_ohlcv(tickers_data, conditions):
    """OHLCVCache DB에서 필요한 OHLCV 데이터를 일괄 조회해 메모리 캐시에 적재."""
    try:
        from .models import OHLCVCache
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
        from .models import OHLCVCache
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
                tg.send_alert(strategy.name, results, strategy_id=strategy.id)
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
    from .models import OHLCVCache
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


# ──────────────────────────────────────────
# 4. 알림 설정 API
# ──────────────────────────────────────────

from .models import AlertSetting
from . import telegram as tg
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
import json as _json


@require_GET
def alert_get(request, strategy_id):
    """GET: 전략의 알림 설정 반환"""
    strategy = get_object_or_404(Strategy, id=strategy_id)
    try:
        a = strategy.alert
        data = {
            'enabled':    a.enabled,
            'alert_hour': a.alert_hour,
            'alert_min':  a.alert_min,
            'exchange':   a.exchange,
            'vol_limit':  a.vol_limit,
        }
    except Exception:
        # RelatedObjectDoesNotExist (AlertSetting.DoesNotExist의 서브클래스)를 포함한
        # 모든 "alert 없음" 예외를 안전하게 처리
        data = {'enabled': False, 'alert_hour': 9, 'alert_min': 0,
                'exchange': 'upbit', 'vol_limit': 0}
    data['tg_configured'] = tg.is_configured()
    return JsonResponse(data)


@csrf_exempt
@require_POST
def alert_save(request, strategy_id):
    """POST: 알림 설정 저장"""
    strategy = get_object_or_404(Strategy, id=strategy_id)
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': '잘못된 요청'}, status=400)

    try:
        alert_hour = 9  # Vercel Hobby 크론 제한(하루 1회)으로 오전 9시 고정
        alert_min  = 0  # 30분 단위 제외, 정각만 사용
        vol_limit  = int(body.get('vol_limit', 0))
    except (ValueError, TypeError) as e:
        return JsonResponse({'ok': False, 'error': f'숫자 형식 오류: {e}'}, status=400)

    enabled   = bool(body.get('enabled', False))
    exchange  = body.get('exchange', 'upbit')
    send_test = bool(body.get('send_test', False))

    AlertSetting.objects.update_or_create(
        strategy=strategy,
        defaults={
            'enabled':    enabled,
            'alert_hour': alert_hour,
            'alert_min':  alert_min,
            'exchange':   exchange,
            'vol_limit':  vol_limit,
        }
    )

    if send_test:
        if not tg.is_configured():
            return JsonResponse({'ok': False, 'error': '텔레그램 환경변수가 설정되지 않았습니다. (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)'})
        res = tg.send_message(f"🔔 [{strategy.name}] 텔레그램 알림 연결 테스트 메시지입니다.")
        if not res['ok']:
            return JsonResponse({'ok': False, 'error': res['error']})

    return JsonResponse({'ok': True})


def process_scan_and_alert(strategy, tickers, conditions):
    """
    주어진 전략과 티커 목록을 대상으로 스캔하고,
    알림 이력 저장 및 12시간 중복 방지 필터링을 거친 결과를 반환합니다.
    """
    from django.utils import timezone
    import datetime

    _bulk_prefetch_ohlcv(tickers, conditions)

    results = []

    def _proc(t_data):
        ticker = t_data['ticker'] if isinstance(t_data, dict) else t_data
        name   = t_data.get('name', ticker.replace('KRW-', '')) if isinstance(t_data, dict) else KOSPI_NAME_MAP.get(ticker, ticker.replace('KRW-', ''))
        try:
            is_match, details, price, volume, change_rate, status = check_strategy(ticker, conditions)
            if is_match and price:
                details_str = ", ".join(list(dict.fromkeys(details)))
                should_notify = True
                AlertHistory.objects.create(
                    strategy=strategy,
                    symbol=ticker,
                    price=price,
                    volume=volume,
                    details=details_str,
                    status=status,
                    is_notified=should_notify
                )
                return {
                    'symbol':         ticker,
                    'name':           name,
                    'price':          price,
                    'volume':         volume,
                    'volume_display': f"{volume / 100_000_000:.1f}억",
                    'status':         status,
                    'details':        details_str,
                    'should_notify':  should_notify
                }
        except Exception as e:
            print(f"Error scanning {ticker}: {e}")
        return None

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        for r in executor.map(_proc, tickers):
            if r:
                results.append(r)
                
    # 거래대금 순으로 정렬
    results.sort(key=lambda x: x.get('volume', 0), reverse=True)
    
    # 텔레그램용 결과: 중복 발송 방지 처리된(should_notify=True) 코인들만 선별
    tg_results = [r for r in results if r['should_notify']]
    
    return results, tg_results


@csrf_exempt
@require_POST
def alert_send_now(request, strategy_id):
    """POST: 즉시 스캔 후 텔레그램 발송"""
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())

    if not conditions:
        return JsonResponse({'ok': False, 'error': '조건이 없습니다.'})
    if not tg.is_configured():
        return JsonResponse({'ok': False, 'error': '텔레그램 환경변수가 설정되지 않았습니다. (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)'})

    try:
        body = _json.loads(request.body)
    except Exception:
        body = {}

    # body 파싱 실패해도 안전하게 기본값 사용
    exchange  = body.get('exchange', 'upbit') or 'upbit'
    try:
        vol_limit_val = body.get('vol_limit')
        vol_limit = int(vol_limit_val) if vol_limit_val is not None else 0
    except (ValueError, TypeError):
        vol_limit = 0

    tickers = _get_tickers(exchange, vol_limit)
    results, tg_results = process_scan_and_alert(strategy, tickers, conditions)

    res = tg.send_alert(strategy.name, tg_results, strategy_id=strategy.id)
    if res['ok']:
        return JsonResponse({'ok': True, 'matched': len(results), 'sent': len(tg_results)})
    return JsonResponse({'ok': False, 'error': res['error']})


# ──────────────────────────────────────────
# 5. 백테스팅 API
# ──────────────────────────────────────────

from .backtest import run_backtest, MAJOR_COINS


@require_GET
def backtest_coins(request):
    """GET: 메이저 코인 목록 반환"""
    return JsonResponse({'coins': MAJOR_COINS})


@csrf_exempt
@require_POST
def backtest_run(request, strategy_id):
    """POST: 백테스팅 실행"""
    strategy   = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())

    if not conditions:
        return JsonResponse({'error': '조건이 없습니다.'}, status=400)

    try:
        body = _json.loads(request.body)
        ticker       = body.get('ticker', 'KRW-BTC')
        candle_count = int(body.get('candle_count', 200))
        sell_mode    = body.get('sell_mode', 'cond_exit')
        sell_param   = float(body.get('sell_param', 5))
        fee_pct      = float(body.get('fee', 0.05))
    except Exception:
        return JsonResponse({'error': '잘못된 요청'}, status=400)

    # candle_count 범위 고정
    if candle_count not in (50, 100, 200, 500):
        candle_count = 200

    # 티커 형식 기본 검증 (KRW-XXX 형태인지만 확인)
    import re as _re
    if not _re.match(r'^KRW-[A-Z0-9]{1,20}$', ticker):
        return JsonResponse({'error': '올바르지 않은 티커 형식'}, status=400)

    result = run_backtest(ticker, conditions, candle_count, sell_mode, sell_param, fee_pct)
    if 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@csrf_exempt
def cron_scan(request):
    """Vercel Cron: 30분 주기로 한국 표준시(KST)를 계산하여, 예약된 활성 알림 스캔 및 텔레그램 발송"""
    from django.http import HttpResponseForbidden
    import traceback
    
    # 보안 검증: Vercel Cron이거나 디버그 시크릿이 있는 경우만 허용
    is_vercel_cron = request.headers.get('x-vercel-cron') == '1'
    is_debug = request.GET.get('secret') == 'wonii_cron_debug'
    is_force = request.GET.get('force') == 'true'
    
    print(f"[CRON_SCAN] Triggered. is_vercel_cron={is_vercel_cron}, is_debug={is_debug}, is_force={is_force}")
    print(f"[CRON_SCAN] Headers: {dict(request.headers)}")
    
    if not is_vercel_cron and not is_debug:
        print("[CRON_SCAN] Security check failed: Forbidden access.")
        return HttpResponseForbidden("권한이 없습니다.")
        
    try:
        from django.utils import timezone
        import datetime
        
        # 한국 표준시(KST) 구하기 (settings.py의 TIME_ZONE='Asia/Seoul' 및 USE_TZ=True 연동)
        now_kst = timezone.localtime(timezone.now())
        print(f"[CRON_SCAN] Current KST time: {now_kst}")
        
        # 가장 가까운 정각(1시간 단위)으로 반올림 (30분 오차 범위 보정)
        rounded_time = now_kst + datetime.timedelta(minutes=30)
        current_hour = rounded_time.hour
        
        # 기본적으로 시간 필터를 적용하여 사용자가 설정한 시간(alert_hour)에만 스캔 수행
        # 단, 수동 강제 테스트(&force=true) 시에는 시간 필터 없이 전체 스캔
        if is_force:
            active_settings = AlertSetting.objects.filter(enabled=True)
            print(f"[CRON_SCAN] (FORCE) Scanning all {active_settings.count()} active settings ignoring time.")
        else:
            active_settings = AlertSetting.objects.filter(
                enabled=True,
                alert_hour=current_hour
            )
            print(f"[CRON_SCAN] Scanning {active_settings.count()} active settings matching KST hour {current_hour}.")
            
        processed_count = 0
        sent_count = 0
        results_summary = []
        warnings = []
        
        if not active_settings.exists():
            if is_force:
                warnings.append("활성화된 알림 설정(AlertSetting)이 존재하지 않습니다. Vercel 배포 시 SQLite 데이터가 초기화되었거나, 웹 페이지에서 알림 설정을 켜지 않았을 수 있습니다.")
            else:
                warnings.append(f"현재 KST {current_hour}시에 예약 활성화된 알림 설정이 없습니다. (만약 즉시 강제 테스트를 원하시면 URL 뒤에 &force=true 를 붙여 접속해 주세요.)")
        
        for setting in active_settings:
            strategy = setting.strategy
            print(f"[CRON_SCAN] Scanning strategy: {strategy.name} (ID: {strategy.id})")
            conditions = list(strategy.conditions.all())
            print(f"[CRON_SCAN] Strategy conditions count: {len(conditions)}")
            if not conditions:
                warn_msg = f"전략 '{strategy.name}'(ID: {strategy.id})에 조건이 존재하지 않아 스킵합니다."
                print(f"[CRON_SCAN] {warn_msg}")
                warnings.append(warn_msg)
                continue
                
            processed_count += 1
            
            # 티커 수집 (설정된 vol_limit 사용, 0인 경우 전체 코인)
            vol_limit = setting.vol_limit
            
            # Vercel 10초 실행 시간제한(Timeout) 방지를 위한 안전 장치 (30개 초과 시 자동으로 30개로 제한)
            safe_limit = 30
            if not vol_limit or vol_limit > safe_limit:
                warn_msg = f"전략 '{strategy.name}': Vercel Hobby 실행시간 제한(10초) 방지를 위해 스캔 코인 수를 {vol_limit if vol_limit else '전체'}개에서 {safe_limit}개로 자동 제한합니다."
                print(f"[CRON_SCAN] {warn_msg}")
                warnings.append(warn_msg)
                vol_limit = safe_limit
                
            tickers = _get_tickers(setting.exchange, vol_limit)
            print(f"[CRON_SCAN] Tickers count for {setting.exchange} (limit {vol_limit}): {len(tickers)}")
            
            results, tg_results = process_scan_and_alert(strategy, tickers, conditions)
            print(f"[CRON_SCAN] Scan results: total matched = {len(results)}, notify list = {len(tg_results)}")
            
            # 텔레그램 발송 (중복 방지 처리된 tg_results 사용)
            if tg.is_configured():
                res = tg.send_alert(strategy.name, tg_results, strategy_id=strategy.id)
                print(f"[CRON_SCAN] Telegram send result: {res}")
                if res.get('ok'):
                    sent_count += 1
                else:
                    warnings.append(f"텔레그램 발송 실패 ({strategy.name}): {res.get('error')}")
                results_summary.append({
                    'strategy': strategy.name,
                    'matched_count': len(results),
                    'sent_count': len(tg_results),
                    'telegram_result': res
                })
            else:
                warn_msg = f"전략 '{strategy.name}': 텔레그램 환경변수(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)가 Vercel에 설정되지 않았습니다."
                print(f"[CRON_SCAN] {warn_msg}")
                warnings.append(warn_msg)
                results_summary.append({
                    'strategy': strategy.name,
                    'matched_count': len(results),
                    'sent_count': len(tg_results),
                    'telegram_result': {'ok': False, 'error': '환경변수 미설정'}
                })
                
        return JsonResponse({
            'ok': True,
            'time': now_kst.strftime('%Y-%m-%d %H:%M:%S KST'),
            'processed': processed_count,
            'sent_alerts': sent_count,
            'warnings': warnings,
            'details': results_summary
        })
        
    except Exception as e:
        err_msg = traceback.format_exc()
        print(f"[CRON_SCAN] Error occurred:\n{err_msg}")
        return JsonResponse({'error': f'크론 수행 중 서버 오류: {str(e)}', 'traceback': err_msg}, status=500)


def strategy_trading(request, strategy_id=None):
    strategies = Strategy.objects.all().order_by('-created_at')
    
    if strategy_id is None:
        first_strat = strategies.first()
        if first_strat:
            return redirect('strategy_detail', strategy_id=first_strat.id)
        strategy = None
        conditions = []
        histories = []
    else:
        return redirect('strategy_detail', strategy_id=strategy_id)
        
    return render(request, 'screener/strategy_trading.html', {
        'strategies': strategies,
        'strategy': strategy,
        'conditions': conditions,
        'histories': histories,
    })


@csrf_exempt
@require_POST
def save_risk_settings(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)
    try:
        body = _json.loads(request.body)
        stop_loss = float(body.get('stop_loss', -8.0))
        take_profit = float(body.get('take_profit', 24.0))
        capital_pct = int(body.get('capital_pct', 20))
        
        strategy.stop_loss = stop_loss
        strategy.take_profit = take_profit
        strategy.capital_pct = capital_pct
        strategy.save()
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@csrf_exempt
@require_POST
def strategy_rename(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)
    try:
        body = _json.loads(request.body)
        new_name = body.get('name', '').strip()
        if not new_name:
            return JsonResponse({'ok': False, 'error': '전략 이름을 입력해주세요.'}, status=400)
        strategy.name = new_name
        strategy.save()
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@require_GET
def strategy_scan_count(request, strategy_id):
    strategy = get_object_or_404(Strategy, id=strategy_id)
    conditions = list(strategy.conditions.all())
    
    if not conditions:
        return JsonResponse({'ok': True, 'count': 0})
        
    exchange = request.GET.get('exchange', 'upbit')
    try:
        vol_limit_param = request.GET.get('vol_limit')
        vol_limit = int(vol_limit_param) if vol_limit_param is not None else 0
    except (ValueError, TypeError):
        vol_limit = 0
        
    tf_override = request.GET.get('timeframe')
    if tf_override:
        for c in conditions:
            c.timeframe = tf_override

    tickers = _get_tickers(exchange, vol_limit)
    _bulk_prefetch_ohlcv(tickers, conditions)
    results = []
    error_occurred = False

    def process_ticker(t_data):
        ticker = t_data['ticker'] if isinstance(t_data, dict) else t_data
        try:
            is_match, details, price, volume, change_rate, status = check_strategy(ticker, conditions)
            if price is None:
                return "API_ERROR"
            if is_match:
                unique_details = list(dict.fromkeys(details))
                return {
                    'symbol':         ticker,
                    'price':          price,
                    'details':        ", ".join(unique_details),
                    'volume':         volume,
                    'volume_display': f"{volume / 100_000_000:.1f}억",
                    'status':         status,
                }
        except Exception:
            pass
        return None

    api_error_count = 0
    none_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_ticker, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res == "API_ERROR":
                error_occurred = True
                api_error_count += 1
            elif res:
                results.append(res)
            else:
                none_count += 1

    results.sort(key=lambda x: x.get('volume', 0), reverse=True)
    last_updated = timezone.now()
    cache_key = f"strategy_results_{strategy_id}_{exchange}_{vol_limit}"

    cache.set(cache_key, {
        'results':            results,
        'rate_limit_warning': error_occurred,
        'last_updated':       last_updated,
    }, timeout=300)

    debug = request.GET.get('debug') == '1'
    resp = {'ok': True, 'count': len(results)}
    if debug:
        from .models import OHLCVCache
        active_timeframes = list(set(c.timeframe for c in conditions))
        db_count = OHLCVCache.objects.filter(timeframe__in=active_timeframes).count()
        resp['_debug'] = {
            'total_tickers': len(tickers),
            'api_error_count': api_error_count,
            'none_count': none_count,
            'active_timeframes': active_timeframes,
            'ohlcvcache_rows': db_count,
            'conditions': [{'tf': c.timeframe, 'left': c.left_indicator, 'op': c.operator, 'right': c.right_indicator} for c in conditions],
        }
    return JsonResponse(resp)







