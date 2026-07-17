import pyupbit
import pandas as pd
import time
import numpy as np
import threading
import random
import datetime
import FinanceDataReader as fdr
from django.core.cache import cache

_rate_limit_lock = threading.Lock()
_last_request_time = 0.0
_min_interval = 0.12  # ∞╡£∞åî 0.12∞┤ê Ω░äΩ▓⌐ (∞┤êδï╣ ∞╡£δîÇ ~8.3φÜî ∞Üö∞▓¡)


def get_ohlcv_with_retry(ticker, interval, count=200, retries=5, delay=0.4):
    """API φÿ╕∞╢£ ∞á£φò£∞¥ä Ω│áδáñφòÿ∞ù¼ Ω╕Çδí£δ▓î ∞åìδÅä ∞á£φò£ δ░Å ∞ºÇφä░ ∞₧¼∞ï£δÅäΩ░Ç ∞áü∞Ü⌐δÉ£ OHLCV ∞í░φÜî"""
    # 1. ∞║É∞ï£ φÖò∞¥╕ (5δ╢ä φâÇ∞₧ä∞òä∞¢â, Ω░Ö∞¥Ç ∞óàδ¬⌐/φâÇ∞₧äφöäδáê∞₧ä ∞₧¼∞Üö∞▓¡ ∞ï£ ∞ªë∞ï£ δ░ÿφÖÿ)
    cache_key = f"ohlcv_{ticker}_{interval}_{count}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    # 1.5. DB ∞é¼∞áä ∞║É∞ï£ φÖò∞¥╕ (Pre-fetching)
    try:
        from screener.models import OHLCVCache
        import json
        cached_obj = OHLCVCache.objects.filter(ticker=ticker, timeframe=interval).first()
        if cached_obj and cached_obj.data:
            # Check if it's too old (e.g., > 10 minutes)
            # Since Vercel Cron might not run, we want fresh data.
            # But if we rely on cron-job.org, it's 1 minute.
            # Let's accept up to 15 minutes old data as "fresh enough" for a fallback.
            # API φÅ┤δ░▒∞¥┤ ∞░¿δï¿δÉÿ∞ùê∞£╝δ»Çδí£ ∞║É∞ï£ ∞£áφÜ¿Ω╕░Ω░ä∞¥ä δäëδäëφ₧ê(7∞¥╝) ∞₧í∞òä δ¼┤∞í░Ω▒┤ ∞║É∞ï£δÑ╝ φÖ£∞Ü⌐φòÿδÅäδí¥ φò⌐δïêδïñ.
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - cached_obj.updated_at).total_seconds() < 604800:
                json_str = json.dumps(cached_obj.data)
                import io
                df = pd.read_json(io.StringIO(json_str), orient='split')
                df.index.name = None # Upbit df has no index name
                cache.set(cache_key, df.tail(count), 180)
                return df.tail(count)
    except Exception as e:
        print(f"OHLCVCache read error for {ticker}: {e}")

    # ∞é¼∞Ü⌐∞₧É ∞Üö∞▓¡∞ùÉ δö░δ¥╝ ∞ïñ∞ï£Ω░ä ∞Ö╕δ╢Ç API(∞ùàδ╣äφè╕, FDR) ∞í░φÜî φå╡∞ïá∞¥ä ∞áäδ⌐┤ ∞░¿δï¿φòÿΩ│á ∞ÿñ∞ºü ∞║É∞ï£δºî ∞¥ÿ∞í┤φòÿδÅäδí¥ δ│ÇΩ▓╜
    # (∞ïñ∞ï£Ω░ä φå╡∞ïá δîÇΩ╕░δí£ ∞¥╕φò£ ∞èñφü¼δª¼δï¥ ∞ºÇ∞ù░ ∞Öäδ▓╜ ∞░¿δï¿)
    return None


def calculate_rsi(ohlc: pd.DataFrame, period: int = 14):
    """
    Wilder∞¥ÿ ∞ºÇ∞êÿ∞¥┤δÅÖφÅëΩ╖á δ░⌐∞ï¥∞£╝δí£ RSI Ω│ä∞é░.
    alpha=1/period δÑ╝ ∞é¼∞Ü⌐φò┤ ∞ïñ∞á£ 14-period RSI∞ùÉ ∞áòφÖòφ₧ê δîÇ∞¥æ.
    (Ω╕░∞í┤ com=period-1 δ░⌐∞ï¥∞¥Ç span=2*period-1∞ùÉ φò┤δï╣φòÿ∞ù¼ δèÉδª¼Ω▓î ∞êÿδá┤φòÿδèö δ¼╕∞á£ ∞₧ê∞ùê∞¥î)
    """
    delta = ohlc["close"].diff()
    ups = delta.clip(lower=0)
    downs = (-delta).clip(lower=0)

    au = ups.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    ad = downs.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # adΩ░Ç 0∞¥╕ Ω▓╜∞Ü░(φòÿδ¥╜∞¥┤ ∞áäφÿÇ ∞ùåδèö Ω╡¼Ω░ä) ZeroDivisionError δ░⌐∞ºÇ
    RS = au / ad.replace(0, float('nan'))
    rsi = 100 - (100 / (1 + RS))
    return pd.Series(rsi, name="RSI")


def calculate_wma(series: pd.Series, period: int):
    """Ω░Ç∞ñæ∞¥┤δÅÖφÅëΩ╖á(WMA) Ω│ä∞é░"""
    if len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)
    weights = np.arange(1, period + 1)
    wma = series.rolling(window=period).apply(lambda prices: np.dot(prices, weights) / weights.sum(), raw=True)
    return wma


def calculate_bollinger(series: pd.Series, period: int = 20, std: float = 2.0):
    """δ│╝δª░∞áÇ δ░┤δô£ Ω│ä∞é░. ∞âüδï¿, ∞ñæΩ░ä, φòÿδï¿ δ░┤δô£δÑ╝ φÅ¼φò¿φòÿδèö DataFrame δ░ÿφÖÿ"""
    ma = series.rolling(window=period).mean()
    mstd = series.rolling(window=period).std()
    upper = ma + (mstd * std)
    lower = ma - (mstd * std)
    return pd.DataFrame({'BB_UPPER': upper, 'BB_MIDDLE': ma, 'BB_LOWER': lower}, index=series.index)




def calculate_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    φòÿ∞¥┤φé¿∞òä∞ï£ ∞║öδôñ Ω│ä∞é░.
    HA_close  = (open + high + low + close) / 4
    HA_open   = (prev_HA_open + prev_HA_close) / 2  (∞▓½ δ┤ë∞¥Ç (open+close)/2)
    HA_high   = max(high, HA_open, HA_close)
    HA_low    = min(low,  HA_open, HA_close)
    """
    ha_close = (df['open'] + df['high'] + df['low'] + df['close']) / 4

    ha_open = ha_close.copy()
    ha_open.iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2

    ha_high = pd.concat([df['high'], ha_open, ha_close], axis=1).max(axis=1)
    ha_low  = pd.concat([df['low'],  ha_open, ha_close], axis=1).min(axis=1)

    return pd.DataFrame({
        'ha_open':  ha_open,
        'ha_high':  ha_high,
        'ha_low':   ha_low,
        'ha_close': ha_close,
    }, index=df.index)


def check_ha_pattern(df: pd.DataFrame, pattern: str, param: int, offset: int) -> bool:
    """
    φòÿ∞¥┤φé¿∞òä∞ï£ φî¿φä┤ ∞╢⌐∞í▒ ∞ù¼δ╢Ç δ░ÿφÖÿ.
    pattern: HA_BULL / HA_BEAR / HA_BULL_N / HA_BEAR_N / HA_NO_LOWER / HA_NO_UPPER
    param  : HA_BULL_N / HA_BEAR_N ∞¥ÿ ∞ù░∞åì Nδ┤ë ∞êÿ
    offset : 0 = φÿä∞₧¼δ┤ë, 1 = 1δ┤ë ∞áä ...
    """
    ha = calculate_heikin_ashi(df)
    target = -1 - offset

    if abs(target) > len(ha):
        return False

    if pattern == 'HA_BULL':
        return float(ha['ha_close'].iloc[target]) >= float(ha['ha_open'].iloc[target])

    if pattern == 'HA_BEAR':
        return float(ha['ha_close'].iloc[target]) < float(ha['ha_open'].iloc[target])

    if pattern == 'HA_BULL_N':
        n = max(1, param)
        if len(ha) < n + abs(offset):
            return False
        for i in range(n):
            idx = target - i
            if abs(idx) > len(ha):
                return False
            if float(ha['ha_close'].iloc[idx]) < float(ha['ha_open'].iloc[idx]):
                return False
        return True

    if pattern == 'HA_BEAR_N':
        n = max(1, param)
        if len(ha) < n + abs(offset):
            return False
        for i in range(n):
            idx = target - i
            if abs(idx) > len(ha):
                return False
            if float(ha['ha_close'].iloc[idx]) >= float(ha['ha_open'].iloc[idx]):
                return False
        return True

    if pattern == 'HA_NO_LOWER':
        # ∞òäδ₧½Ω╝¼δª¼ ∞ùå∞¥î: HA∞áÇΩ░Ç Γëê HA∞ï£Ω░Ç (∞ÿñ∞░¿ 0.01% φùê∞Ü⌐)
        ha_o = float(ha['ha_open'].iloc[target])
        ha_l = float(ha['ha_low'].iloc[target])
        return abs(ha_l - ha_o) / (ha_o + 1e-10) < 0.0001

    if pattern == 'HA_NO_UPPER':
        # ∞£ùΩ╝¼δª¼ ∞ùå∞¥î: HAΩ│áΩ░Ç Γëê HA∞óàΩ░Ç (∞ÿñ∞░¿ 0.01% φùê∞Ü⌐)
        ha_c = float(ha['ha_close'].iloc[target])
        ha_h = float(ha['ha_high'].iloc[target])
        return abs(ha_h - ha_c) / (ha_c + 1e-10) < 0.0001

    return False


def get_required_len(indicator_type, param):
    """∞ºÇφæ£ Ω│ä∞é░∞ùÉ φòä∞Üöφò£ ∞╡£∞åî δì░∞¥┤φä░ Ω╕╕∞¥┤ δ░ÿφÖÿ"""
    if indicator_type in ('VAL', 'CLOSE', 'VOLUME'):
        return 0
    if indicator_type == 'VOLUME_PREV':
        return 1
    if indicator_type == 'VOLUME_MA':
        return param
    if indicator_type == 'IC_TENKAN':
        return param
    if indicator_type == 'IC_KIJUN':
        return param
    if indicator_type == 'IC_SPAN_A':
        return 52
    if indicator_type == 'IC_SPAN_B':
        return 78
    if indicator_type == 'IC_CHIKOU':
        return 0
    if indicator_type == 'IC_CHIKOU_REF':
        return param
    return param


def check_strategy(ticker, conditions, current_price=None):
    """
    φè╣∞áò ∞╜ö∞¥╕∞¥┤ ∞ú╝∞û┤∞ºä ∞áäδ₧╡(∞í░Ω▒┤ δª¼∞èñφè╕)∞¥ä δ¬¿δæÉ δºî∞í▒φòÿδèö∞ºÇ φÖò∞¥╕.
    ∞í░Ω▒┤∞¥┤ δ╣ä∞û┤∞₧ê∞£╝δ⌐┤ δºñ∞╣¡φòÿ∞ºÇ ∞òè∞¥î. 
    
    Returns:
        (is_match, details, last_price, volume, status)
        status: 'new', 'maintained', or None
    """
    # ∞í░Ω▒┤∞¥┤ ∞ùå∞£╝δ⌐┤ δ¬¿δôá ∞╜ö∞¥╕∞¥┤ φå╡Ω│╝δÉÿδèö δ▓äΩ╖╕ δ░⌐∞ºÇ
    if not conditions:
        return False, [], None, 0, None

    try:
        data_cache = {}
        details = []
        last_price = None
        volume = 0

        def _check_for_offset(base_offset):
            """Helper to check all conditions for a given base offset."""
            for cond in conditions:
                if cond.timeframe not in data_cache:
                    df = get_ohlcv_with_retry(ticker, interval=cond.timeframe)
                    if df is None: return False
                    data_cache[cond.timeframe] = df
                
                df = data_cache[cond.timeframe]
                
                cond_met = False
                # cond.offset∞¥┤ 'nδ┤ë ∞¥┤δé┤'δÑ╝ ∞¥ÿδ»╕φòÿδ»Çδí£, 0δ╢Çφä░ cond.offsetΩ╣î∞ºÇ δ¬¿δôá δ┤ë∞¥ä Ω▓Ç∞é¼φòÿ∞ù¼ φòÿδéÿδ¥╝δÅä δºî∞í▒φòÿδ⌐┤ True
                for i in range(cond.offset + 1):
                    total_offset = base_offset + i
                    
                    required_len = max(
                        get_required_len(cond.left_indicator, cond.left_param),
                        get_required_len(cond.right_indicator, cond.right_param)
                    ) + total_offset + 2  # +2 for cross_up/down

                    if len(df) < required_len:
                        continue
                        
                    # ΓöÇΓöÇ φòÿ∞¥┤φé¿∞òä∞ï£ φî¿φä┤ ∞í░Ω▒┤ ΓöÇΓöÇ
                    ha_patterns = ('HA_BULL','HA_BEAR','HA_BULL_N','HA_BEAR_N','HA_NO_LOWER','HA_NO_UPPER')
                    if cond.left_indicator in ha_patterns:
                        if check_ha_pattern(df, cond.left_indicator, cond.left_param, total_offset):
                            cond_met = True
                            break
                        continue
                        
                    bb_std = cond.bb_std if cond.bb_std is not None else 2.0
                    left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, total_offset, bb_std=bb_std)
                    right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, total_offset, bb_std=bb_std)

                    if left_val is None or right_val is None or pd.isna(left_val) or pd.isna(right_val):
                        continue

                    if cond.operator == 'btw':
                        if cond.left_indicator == 'VOLUME':
                            max_multiplier = cond.left_param / 100.0
                            max_val = get_indicator_value(df, cond.right_indicator, cond.right_param, total_offset, bb_std=max_multiplier)
                        else:
                            max_val = cond.bb_std if cond.bb_std is not None else float('inf')
                        
                        if (right_val <= left_val <= max_val):
                            cond_met = True
                            break
                    elif cond.operator in ('cross_up', 'cross_down'):
                        prev_offset = total_offset + 1
                        left_val_prev = get_indicator_value(df, cond.left_indicator, cond.left_param, prev_offset, bb_std=bb_std)
                        right_val_prev = get_indicator_value(df, cond.right_indicator, cond.right_param, prev_offset, bb_std=bb_std)
                        
                        if left_val_prev is None or right_val_prev is None or pd.isna(left_val_prev) or pd.isna(right_val_prev):
                            continue
                            
                        if cond.operator == 'cross_up':
                            if left_val_prev <= right_val_prev and left_val > right_val:
                                cond_met = True
                                break
                        elif cond.operator == 'cross_down':
                            if left_val_prev >= right_val_prev and left_val < right_val:
                                cond_met = True
                                break
                    else:
                        op_map = {'gt': left_val > right_val, 'lt': left_val < right_val, 'gte': left_val >= right_val, 'lte': left_val <= right_val}
                        if op_map.get(cond.operator):
                            cond_met = True
                            break
                            
                if not cond_met:
                    return False
            return True

        # 1. φÿä∞₧¼ δ┤ë(base_offset=0) ∞í░Ω▒┤ φÖò∞¥╕
        is_match_current = _check_for_offset(0)

        # φÿä∞₧¼Ω░Ç∞ÖÇ Ω▒░δ₧ÿδîÇΩ╕ê∞¥Ç δì░∞¥┤φä░Ω░Ç ∞₧ê∞£╝δ⌐┤ φò¡∞âü Ω░Ç∞á╕∞ÿ┤
        if conditions and conditions[0].timeframe in data_cache:
            df = data_cache[conditions[0].timeframe]
            if not df.empty:
                last_price = df['close'].iloc[-1]
                volume = df['value'].iloc[-1] if 'value' in df.columns else df['volume'].iloc[-1]

        if not is_match_current:
            return False, [], last_price, volume, None

        # 2. ∞¥┤∞áä δ┤ë(base_offset=1) ∞í░Ω▒┤ φÖò∞¥╕
        is_match_previous = _check_for_offset(1)
        status = 'maintained' if is_match_previous else 'new'

        # 3. φÿä∞₧¼ δ┤ë Ω╕░∞ñÇ details ∞êÿ∞ºæ
        ha_patterns = ('HA_BULL','HA_BEAR','HA_BULL_N','HA_BEAR_N','HA_NO_LOWER','HA_NO_UPPER')
        HA_LABEL = {
            'HA_BULL': 'HA ∞ûæδ┤ë', 'HA_BEAR': 'HA ∞¥îδ┤ë',
            'HA_BULL_N': f'HA ∞ù░∞åì ∞ûæδ┤ë', 'HA_BEAR_N': f'HA ∞ù░∞åì ∞¥îδ┤ë',
            'HA_NO_LOWER': 'HA ∞òäδ₧½Ω╝¼δª¼ ∞ùå∞¥î', 'HA_NO_UPPER': 'HA ∞£ùΩ╝¼δª¼ ∞ùå∞¥î',
        }
        for cond in conditions:
            df = data_cache[cond.timeframe]
            if cond.left_indicator in ha_patterns:
                label = HA_LABEL.get(cond.left_indicator, cond.left_indicator)
                if 'N' in cond.left_indicator:
                    label = label.replace('∞ù░∞åì', f'∞ù░∞åì{cond.left_param}δ┤ë')
                details.append(label)
                continue
            bb_std = cond.bb_std if cond.bb_std is not None else 2.0
            left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, cond.offset, bb_std=bb_std)
            right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, cond.offset, bb_std=bb_std)

            if cond.operator == 'btw':
                if cond.left_indicator == 'VOLUME':
                    max_multiplier = cond.left_param / 100.0
                    max_val = get_indicator_value(df, cond.right_indicator, cond.right_param, cond.offset, bb_std=max_multiplier)
                    details.append(f"Ω▒░δ₧ÿδƒë: {left_val:,.0f} (δ▓ö∞£ä: {right_val:,.0f} ~ {max_val:,.0f})")
                else:
                    max_val = cond.bb_std if cond.bb_std is not None else float('inf')
                    details.append(f"{cond.left_indicator}({cond.left_param}): {left_val:.2f} (δ▓ö∞£ä: {right_val:.2f} ~ {max_val:.2f})")
            else:
                if cond.left_indicator != 'VAL':
                    if left_val is not None and not pd.isna(left_val):
                        if cond.left_indicator == 'VOLUME':
                            details.append(f"Ω▒░δ₧ÿδƒë: {left_val:,.0f}")
                        else:
                            details.append(f"{cond.left_indicator}({cond.left_param}): {left_val:.2f}")
                if cond.right_indicator != 'VAL':
                    if right_val is not None and not pd.isna(right_val):
                        if 'VOLUME' in cond.right_indicator:
                            details.append(f"{cond.right_indicator}: {right_val:,.0f}")
                        else:
                            details.append(f"{cond.right_indicator}({cond.right_param}): {right_val:.2f}")
        
        return True, details, last_price, volume, status

    except Exception as e:
        print(f"Error checking {ticker}: {e}")
        return False, [], None, 0, None


def get_indicator_value(df, indicator_type, param, offset, bb_std=2.0):
    """
    DataFrame∞ùÉ∞ä£ φè╣∞áò ∞ï£∞áÉ(offset)∞¥ÿ ∞ºÇφæ£Ω░Æ∞¥ä δ░ÿφÖÿ.
    offset=0: Ω░Ç∞₧Ñ ∞╡£Ω╖╝ δ┤ë, offset=1: 1δ┤ë ∞áä
    """
    target_idx = -1 - offset

    if abs(target_idx) > len(df):
        return None

    if indicator_type == 'MA':
        if param < 1: return None
        ma = df['close'].rolling(window=param).mean()
        val = ma.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'EMA':
        if param < 1: return None
        ema = df['close'].ewm(span=param, adjust=False).mean()
        val = ema.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'WMA':
        if param < 1: return None
        wma = calculate_wma(df['close'], period=param)
        val = wma.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'RSI':
        if param < 1: return None
        rsi = calculate_rsi(df, period=param)
        val = rsi.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type in ('BB_UPPER', 'BB_MIDDLE', 'BB_LOWER'):
        if param < 1: return None
        std = bb_std if bb_std is not None else 2.0
        bb = calculate_bollinger(df['close'], period=param, std=std)
        val = bb[indicator_type].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VAL':
        return float(param)

    elif indicator_type == 'CLOSE':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VOLUME':
        val = df['volume'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VOLUME_PREV':
        prev_idx = target_idx - 1
        if abs(prev_idx) > len(df): return None
        val = df['volume'].iloc[prev_idx]
        multiplier = bb_std if bb_std is not None else 1.0
        val = val * multiplier
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'VOLUME_MA':
        if param < 1: return None
        start_idx = target_idx - param
        end_idx = target_idx
        if abs(start_idx) > len(df): return None
        vol_slice = df['volume'].iloc[start_idx : end_idx]
        if vol_slice.empty: return None
        avg_val = vol_slice.mean()
        multiplier = bb_std if bb_std is not None else 1.0
        val = avg_val * multiplier
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_TENKAN':
        p = max(1, param)
        high_val = df['high'].rolling(window=p).max()
        low_val = df['low'].rolling(window=p).min()
        val = ((high_val + low_val) / 2).iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_KIJUN':
        p = max(1, param)
        high_val = df['high'].rolling(window=p).max()
        low_val = df['low'].rolling(window=p).min()
        val = ((high_val + low_val) / 2).iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_SPAN_A':
        high_9 = df['high'].rolling(window=9).max()
        low_9 = df['low'].rolling(window=9).min()
        tenkan = (high_9 + low_9) / 2

        high_26 = df['high'].rolling(window=26).max()
        low_26 = df['low'].rolling(window=26).min()
        kijun = (high_26 + low_26) / 2

        span_a = (tenkan + kijun) / 2
        span_a_shifted = span_a.shift(param)
        val = span_a_shifted.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_SPAN_B':
        high_52 = df['high'].rolling(window=52).max()
        low_52 = df['low'].rolling(window=52).min()
        span_b = (high_52 + low_52) / 2
        span_b_shifted = span_b.shift(param)
        val = span_b_shifted.iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_CHIKOU':
        val = df['close'].iloc[target_idx]
        return None if pd.isna(val) else float(val)

    elif indicator_type == 'IC_CHIKOU_REF':
        val = df['close'].iloc[target_idx - param] if abs(target_idx - param) <= len(df) else None
        return None if pd.isna(val) else float(val)

    return None
