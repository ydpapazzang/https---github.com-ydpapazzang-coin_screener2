import pathlib
import json

p = pathlib.Path('coinscreener/screener/views.py')
text = p.read_text(encoding='utf-8')

start_marker = 'def cron_prefetch(request):'
end_marker = 'def trigger_migrate(request):'

parts = text.split(start_marker)
before = parts[0]
rest = parts[1].split(end_marker)
body = rest[0]
after = end_marker + rest[1]

new_body = '''
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
        limit = 15
        
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
                
        tickers_info = _get_tickers("upbit", 150)
        
        tasks = []
        for t_info in tickers_info:
            for tf in active_timeframes:
                tasks.append({"ticker": t_info["ticker"], "timeframe": tf})
                
        total_tasks = len(tasks)
        
        if start_idx >= total_tasks:
            start_idx = 0
            
        end_idx = min(start_idx + limit, total_tasks)
        batch_tasks = tasks[start_idx:end_idx]
        
        success_count = 0
        error_count = 0
        
        import concurrent.futures
        import pyupbit
        
        def fetch_and_save(task):
            ticker = task["ticker"]
            tf = task["timeframe"]
            try:
                df = pyupbit.get_ohlcv(ticker, interval=tf, count=200)
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
'''

p.write_text(before + start_marker + new_body + after, encoding='utf-8')
