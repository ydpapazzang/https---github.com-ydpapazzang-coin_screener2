"""사용자 조건식 전략용 보수적 백테스팅 엔진.

- 신호는 봉 마감 시점에 확정하고 주문은 다음 기준봉 시가에 체결한다.
- 서로 다른 시간봉은 배열 위치가 아닌 실제 봉 확정 시각으로 정렬한다.
- 수수료와 슬리피지를 매수·매도 양쪽에 반영한다.
"""
import pyupbit
import pandas as pd
import numpy as np
from .engine import get_indicator_value, check_ha_pattern, get_required_len


MAJOR_COINS = [
    ('KRW-BTC',  'BTC'),
    ('KRW-ETH',  'ETH'),
    ('KRW-XRP',  'XRP'),
    ('KRW-SOL',  'SOL'),
    ('KRW-DOGE', 'DOGE'),
    ('KRW-ADA',  'ADA'),
    ('KRW-AVAX', 'AVAX'),
    ('KRW-DOT',  'DOT'),
    ('KRW-LINK', 'LINK'),
    ('KRW-TRX',  'TRX'),
]


def _candle_available_at(dates, timeframe):
    """각 봉이 완전히 확정되어 사용할 수 있는 시각을 계산한다."""
    dates = pd.to_datetime(dates)
    if timeframe.startswith('minute'):
        try:
            minutes = int(timeframe.replace('minute', ''))
        except ValueError:
            minutes = 1
        return dates + pd.to_timedelta(minutes, unit='m')
    if timeframe == 'week':
        return dates + pd.to_timedelta(7, unit='D')
    if timeframe == 'month':
        return dates + pd.offsets.MonthBegin(1)
    return dates + pd.to_timedelta(1, unit='D')


def _prepare_frame(df, timeframe):
    frame = df.reset_index().rename(columns={'index': 'date'}).copy()
    frame['date'] = pd.to_datetime(frame['date'])
    frame = frame.sort_values('date').drop_duplicates('date').reset_index(drop=True)
    frame['_available_at'] = _candle_available_at(frame['date'], timeframe)
    return frame


def _condition_row_index(df, signal_time):
    available = df['_available_at']
    return int(available.searchsorted(pd.Timestamp(signal_time), side='right') - 1)


def _check_conditions_at(df_map, conditions, signal_time) -> bool:
    """signal_time까지 확정된 데이터만 사용해 모든 조건을 평가한다."""
    for cond in conditions:
        df = df_map.get(cond.timeframe)
        if df is None:
            return False

        row_idx = _condition_row_index(df, signal_time)
        if row_idx < 0:
            return False
        base_offset = (len(df) - 1) - row_idx
        matched = False
        # 실시간 검색과 동일하게 'N봉 이내'는 0..N 중 하나라도 충족함을 뜻한다.
        for lookback in range(cond.offset + 1):
            offset = base_offset + lookback
            ha_patterns = ('HA_BULL', 'HA_BEAR', 'HA_BULL_N', 'HA_BEAR_N', 'HA_NO_LOWER', 'HA_NO_UPPER')
            if cond.left_indicator in ha_patterns:
                if check_ha_pattern(df, cond.left_indicator, cond.left_param, offset):
                    matched = True
                    break
                continue

            bb_std = cond.bb_std if cond.bb_std is not None else 2.0
            lv = get_indicator_value(df, cond.left_indicator, cond.left_param, offset, bb_std=bb_std)
            rv = get_indicator_value(df, cond.right_indicator, cond.right_param, offset, bb_std=bb_std)
            if lv is None or rv is None:
                continue

            if cond.operator == 'btw':
                if cond.left_indicator == 'VOLUME':
                    max_multiplier = cond.left_param / 100.0
                    max_val = get_indicator_value(df, cond.right_indicator, cond.right_param, offset, bb_std=max_multiplier)
                else:
                    max_val = cond.bb_std if cond.bb_std is not None else float('inf')
                matched = max_val is not None and rv <= lv <= max_val
            elif cond.operator in ('cross_up', 'cross_down'):
                prev_offset = offset + 1
                lv_prev = get_indicator_value(df, cond.left_indicator, cond.left_param, prev_offset, bb_std=bb_std)
                rv_prev = get_indicator_value(df, cond.right_indicator, cond.right_param, prev_offset, bb_std=bb_std)
                if lv_prev is None or rv_prev is None:
                    continue
                threshold = max(0.0, float(getattr(cond, 'threshold_pct', 0.0) or 0.0)) / 100.0
                current_barrier = rv * (1 + threshold if cond.operator == 'cross_up' else 1 - threshold)
                previous_barrier = rv_prev * (1 + threshold if cond.operator == 'cross_up' else 1 - threshold)
                matched = (
                    lv_prev <= previous_barrier and lv > current_barrier
                    if cond.operator == 'cross_up'
                    else lv_prev >= previous_barrier and lv < current_barrier
                )
            else:
                threshold = max(0.0, float(getattr(cond, 'threshold_pct', 0.0) or 0.0)) / 100.0
                barrier = rv
                if cond.operator in ('gt', 'gte'):
                    barrier *= 1 + threshold
                elif cond.operator in ('lt', 'lte'):
                    barrier *= 1 - threshold
                matched = {'gt': lv > barrier, 'lt': lv < barrier,
                           'gte': lv >= barrier, 'lte': lv <= barrier}.get(cond.operator, False)
            if matched:
                break
        if not matched:
            return False
    return True


def run_backtest(ticker: str, conditions: list, candle_count: int,
                 sell_mode: str, sell_param: float, fee_pct: float = 0.05,
                 slippage_pct: float = 0.05) -> dict:
    """
    Parameters
    ----------
    ticker       : 'KRW-BTC' 등
    conditions   : Condition 모델 인스턴스 리스트
    candle_count : 최근 N봉 (50 / 100 / 200 / 500)
    sell_mode    : 'exit_n'  → N봉 후 매도 (sell_param = N)
                   'tp_sl'   → 익절/손절 % (sell_param = % 값, 예: 5.0)
                   'cond_exit' → 조건 이탈 시 매도
    sell_param   : 각 모드에 맞는 숫자 파라미터
    fee_pct      : 편도 매매 수수료 (%)
    slippage_pct : 편도 예상 슬리피지 (%)

    Returns
    -------
    dict with keys:
        trades, equity_curve, total_trades, win_rate,
        avg_return, total_return, max_profit, max_loss
    """
    if not conditions:
        return {'error': '조건이 없습니다.'}

    # 조건에 사용된 타임프레임별 데이터 로드
    timeframes = list({c.timeframe for c in conditions})
    primary_tf = conditions[0].timeframe

    df_map = {}
    for tf in timeframes:
        df = pyupbit.get_ohlcv(ticker, interval=tf, count=max(candle_count + 100, 300))
        if df is None:
            return {'error': f'{ticker} 데이터를 불러올 수 없습니다.'}
        df_map[tf] = _prepare_frame(df, tf)

    primary_df = df_map[primary_tf]
    n = len(primary_df)

    trades      = []
    equity      = 100.0          # 초기 자산 100%
    equity_curve = []
    in_position  = False
    pending_entry = False
    pending_exit = False
    entry_price  = 0.0
    entry_idx    = 0
    entry_date   = None

    # 워밍업: 최소 조건 계산에 필요한 봉 수 + offset 최대값
    max_offset = max((c.offset for c in conditions), default=0)
    warmup = max(
        max((get_required_len(c.left_indicator, c.left_param) + c.offset for c in conditions), default=0),
        max((get_required_len(c.right_indicator, c.right_param) + c.offset for c in conditions), default=0),
        max_offset,
    ) + 5

    start_idx = max(warmup, n - candle_count)

    # 백테스팅 시작 인덱스가 데이터 길이를 초과하면 실행 불가
    if start_idx >= n:
        return {'error': f'데이터가 부족하여 백테스팅을 실행할 수 없습니다. (필요: {warmup}봉, 보유: {n}봉). 더 긴 기간을 선택하거나 지표 기간을 줄여주세요.'}

    fee_ratio = fee_pct / 100.0
    slippage_ratio = slippage_pct / 100.0

    def close_trade(raw_exit_price, exit_date):
        nonlocal equity, in_position, pending_exit
        executed_exit = float(raw_exit_price) * (1 - slippage_ratio)
        gross_ratio = executed_exit / entry_price
        net_ratio = gross_ratio * ((1 - fee_ratio) ** 2)
        ret = (net_ratio - 1) * 100
        equity *= net_ratio
        trades.append({
            'entry_date': entry_date,
            'exit_date': exit_date,
            'entry_price': round(entry_price, 8),
            'exit_price': round(executed_exit, 8),
            'return_pct': round(ret, 2),
        })
        equity_curve.append({'date': exit_date, 'equity': round(equity, 4)})
        in_position = False
        pending_exit = False

    for i in range(start_idx, n):
        open_price = float(primary_df['open'].iloc[i])
        price = float(primary_df['close'].iloc[i])
        high_price = float(primary_df['high'].iloc[i])
        low_price  = float(primary_df['low'].iloc[i])
        date  = str(primary_df['date'].iloc[i])[:10]
        exited_this_bar = False

        # 전 봉 마감 때 확정된 주문만 현재 봉 시가에 체결한다.
        if pending_exit and in_position:
            close_trade(open_price, date)
            exited_this_bar = True

        if pending_entry and not in_position and not exited_this_bar:
            in_position = True
            entry_price = open_price * (1 + slippage_ratio)
            entry_idx = i
            entry_date = date
            pending_entry = False

        if in_position:
            # 매도 시그널 체크
            sell = False
            raw_exit_price = price

            if sell_mode == 'exit_n':
                sell = (i - entry_idx) >= int(sell_param)
            elif sell_mode == 'tp_sl':
                stop_price = entry_price * (1 - sell_param / 100.0)
                target_price = entry_price * (1 + sell_param / 100.0)

                # 같은 봉에서 목표·손절이 모두 닿으면 경로를 알 수 없으므로 손절 우선.
                if low_price <= stop_price:
                    sell = True
                    raw_exit_price = min(open_price, stop_price)
                elif high_price >= target_price:
                    sell = True
                    raw_exit_price = target_price
            elif sell_mode == 'cond_exit':
                signal_time = primary_df['_available_at'].iloc[i]
                if not _check_conditions_at(df_map, conditions, signal_time):
                    if i < n - 1:
                        pending_exit = True
                    else:
                        sell = True

            if sell or i == n - 1:
                close_trade(raw_exit_price, date)
                exited_this_bar = True

        # 현재 봉이 완전히 끝난 뒤 신호를 평가하고, 마지막 봉 신호는 체결하지 않는다.
        if not in_position and not pending_entry and not exited_this_bar and i < n - 1:
            signal_time = primary_df['_available_at'].iloc[i]
            if _check_conditions_at(df_map, conditions, signal_time):
                pending_entry = True

    # 안전하게 시작 날짜 추출 (IndexError 방지용 방어 코드)
    if len(primary_df) > 0:
        safe_idx = max(0, min(start_idx, len(primary_df) - 1))
        start_date = str(primary_df['date'].iloc[safe_idx])[:10]
    else:
        start_date = '2026-06-01'

    if not trades:
        return {
            'trades': [], 'equity_curve': [{'date': start_date, 'equity': 100}],
            'total_trades': 0, 'win_rate': 0,
            'avg_return': 0, 'total_return': 0,
            'max_profit': 0, 'max_loss': 0,
            'mdd': 0.0,
            'sharpe': 0.0,
            'expectancy': 0.0,
            'fee_pct': fee_pct,
            'slippage_pct': slippage_pct,
            'execution_rule': 'signal_close_next_open',
        }

    rets       = [t['return_pct'] for t in trades]
    wins       = [r for r in rets if r > 0]
    losses     = [r for r in rets if r < 0]
    win_rate   = round(len(wins) / len(trades) * 100, 1)
    avg_return = round(sum(rets) / len(rets), 2)
    total_ret  = round(equity - 100, 2)
    max_profit = round(max(rets), 2)
    max_loss   = round(min(rets), 2)

    # MDD (최대 낙폭) 계산
    equities = [100.0] + [item['equity'] for item in equity_curve]
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd
    mdd = round(max_dd, 2)

    # 거래 수익률을 실제 테스트 기간의 연간 거래 빈도로 환산한 샤프 비율
    std_ret = np.std(rets, ddof=1) if len(rets) > 1 else 0.0
    first_entry = pd.Timestamp(trades[0]['entry_date'])
    last_exit = pd.Timestamp(trades[-1]['exit_date'])
    period_days = max((last_exit - first_entry).days, 1)
    trades_per_year = len(trades) * 365.25 / period_days
    sharpe = round(np.mean(rets) / std_ret * np.sqrt(trades_per_year), 2) if std_ret > 0.0 else 0.0

    # 기댓값 (Expectancy) 계산
    win_rate_dec = len(wins) / len(rets)
    loss_rate_dec = len(losses) / len(rets)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    expectancy = round((win_rate_dec * avg_win) - (loss_rate_dec * avg_loss), 2)

    # equity_curve 시작점 추가
    equity_curve = [{'date': start_date, 'equity': 100}] + equity_curve

    return {
        'trades':       trades,          # 개별 거래 내역 전체 반환
        'equity_curve': equity_curve,
        'total_trades': len(trades),
        'win_rate':     win_rate,
        'avg_return':   avg_return,
        'total_return': total_ret,
        'max_profit':   max_profit,
        'max_loss':     max_loss,
        'mdd':          mdd,
        'sharpe':       sharpe,
        'expectancy':   expectancy,
        'fee_pct':      fee_pct,
        'slippage_pct': slippage_pct,
        'execution_rule': 'signal_close_next_open',
    }

