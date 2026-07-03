import re

with open('coinscreener/screener/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Modify _check_for_offset
# The current loop logic inside _check_for_offset needs to be replaced.
# We'll use a regex to replace the inside of `for cond in conditions:`
old_loop_logic = """
            for cond in conditions:
                if cond.timeframe not in data_cache:
                    df = get_ohlcv_with_retry(ticker, interval=cond.timeframe)
                    if df is None: return False
                    data_cache[cond.timeframe] = df
                
                df = data_cache[cond.timeframe]
                total_offset = cond.offset + base_offset

                # 지표 계산에 필요한 최소 데이터 길이 검증
                required_len = max(
                    get_required_len(cond.left_indicator, cond.left_param),
                    get_required_len(cond.right_indicator, cond.right_param)
                ) + total_offset + 1

                if len(df) < required_len: return False

                # ── 하이킨아시 패턴 조건 ──
                ha_patterns = ('HA_BULL','HA_BEAR','HA_BULL_N','HA_BEAR_N','HA_NO_LOWER','HA_NO_UPPER')
                if cond.left_indicator in ha_patterns:
                    pattern = cond.left_indicator
                    param   = cond.left_param
                    if not check_ha_pattern(df, pattern, param, total_offset):
                        return False
                    continue

                bb_std = cond.bb_std if cond.bb_std is not None else 2.0
                left_val = get_indicator_value(df, cond.left_indicator, cond.left_param, total_offset, bb_std=bb_std)
                right_val = get_indicator_value(df, cond.right_indicator, cond.right_param, total_offset, bb_std=bb_std)

                if left_val is None or right_val is None or pd.isna(left_val) or pd.isna(right_val):
                    return False

                if cond.operator == 'btw':
                    if cond.left_indicator == 'VOLUME':
                        max_multiplier = cond.left_param / 100.0
                        max_val = get_indicator_value(df, cond.right_indicator, cond.right_param, total_offset, bb_std=max_multiplier)
                    else:
                        max_val = cond.bb_std if cond.bb_std is not None else float('inf')
                    
                    if not (right_val <= left_val <= max_val):
                        return False
                else:
                    op_map = {'gt': left_val > right_val, 'lt': left_val < right_val, 'gte': left_val >= right_val, 'lte': left_val <= right_val}
                    if not op_map.get(cond.operator):
                        return False
"""

new_loop_logic = """
            for cond in conditions:
                if cond.timeframe not in data_cache:
                    df = get_ohlcv_with_retry(ticker, interval=cond.timeframe)
                    if df is None: return False
                    data_cache[cond.timeframe] = df
                
                df = data_cache[cond.timeframe]
                
                cond_met = False
                # cond.offset이 'n봉 이내'를 의미하므로, 0부터 cond.offset까지 모든 봉을 검사하여 하나라도 만족하면 True
                for i in range(cond.offset + 1):
                    total_offset = base_offset + i
                    
                    required_len = max(
                        get_required_len(cond.left_indicator, cond.left_param),
                        get_required_len(cond.right_indicator, cond.right_param)
                    ) + total_offset + 2  # +2 for cross_up/down

                    if len(df) < required_len:
                        continue
                        
                    # ── 하이킨아시 패턴 조건 ──
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
"""

# Replace in content using standard string replace (to avoid regex escaping issues)
if old_loop_logic.strip() in content.strip():
    print("Match found, replacing...")
    pass
else:
    # Just to be safe, I'll use regex for the whole block from 'for cond in conditions:' to 'return True'
    pattern = r'            for cond in conditions:.*?return True'
    content = re.sub(pattern, new_loop_logic.strip() + '\n            return True', content, flags=re.DOTALL)

with open('coinscreener/screener/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Updated engine.py')
