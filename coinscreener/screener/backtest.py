"""
백테스팅 엔진
- 매수: 해당 봉에서 전략 조건 충족 시 진입
- 매도: 3가지 모드 (N봉 후 / 익절·손절 % / 조건 이탈 시)
"""
import pyupbit
import pandas as pd
import numpy as np
from .engine import get_indicator_value, calculate_bollinger, calculate_rsi, calculate_wma


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


def _check_conditions_at(df_map, conditions, idx: int) -> bool:
    """df_map의 idx 시점에서 조건 충족 여부 확인 (offset 무시, 항상 idx 기준)"""
    for cond in conditions:
        df = df_map.get(cond.timeframe)
        if df is None or idx >= len(df):
            return False
        offset = (len(df) - 1) - idx   # idx를 offset으로 변환
        lv = get_indicator_value(df, cond.left_indicator,  cond.left_param,  offset)
        rv = get_indicator_value(df, cond.right_indicator, cond.right_param, offset)
        if lv is None or rv is None:
            return False
        ops = {'gt': lv>rv, 'lt': lv<rv, 'gte': lv>=rv, 'lte': lv<=rv}
        if not ops.get(cond.operator, False):
            return False
    return True


def run_backtest(ticker: str, conditions: list, candle_count: int,
                 sell_mode: str, sell_param: float) -> dict:
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
        df_map[tf] = df.reset_index().rename(columns={'index': 'date'})

    primary_df = df_map[primary_tf]
    n = len(primary_df)

    trades      = []
    equity      = 100.0          # 초기 자산 100%
    equity_curve = []
    in_position  = False
    entry_price  = 0.0
    entry_idx    = 0
    entry_date   = None

    # 워밍업: 최소 조건 계산에 필요한 봉 수
    warmup = max(
        max((c.left_param  for c in conditions if c.left_indicator  not in ('VAL','CLOSE')), default=0),
        max((c.right_param for c in conditions if c.right_indicator not in ('VAL','CLOSE')), default=0),
    ) + 5

    start_idx = max(warmup, n - candle_count)

    for i in range(start_idx, n):
        price = float(primary_df['close'].iloc[i])
        date  = str(primary_df['date'].iloc[i])[:10]

        if not in_position:
            # 매수 시그널 체크
            if _check_conditions_at(df_map, conditions, i):
                in_position = True
                entry_price = price
                entry_idx   = i
                entry_date  = date
        else:
            # 매도 시그널 체크
            sell = False
            if sell_mode == 'exit_n':
                sell = (i - entry_idx) >= int(sell_param)
            elif sell_mode == 'tp_sl':
                change = (price - entry_price) / entry_price * 100
                sell = (change >= sell_param) or (change <= -sell_param)
            elif sell_mode == 'cond_exit':
                sell = not _check_conditions_at(df_map, conditions, i)

            if sell or i == n - 1:
                ret    = (price - entry_price) / entry_price * 100
                equity = equity * (1 + ret / 100)
                trades.append({
                    'entry_date':  entry_date,
                    'exit_date':   date,
                    'entry_price': entry_price,
                    'exit_price':  price,
                    'return_pct':  round(ret, 2),
                })
                in_position = False
                equity_curve.append({'date': date, 'equity': round(equity, 4)})

    if not trades:
        return {
            'trades': [], 'equity_curve': [{'date': str(primary_df['date'].iloc[start_idx])[:10], 'equity': 100}],
            'total_trades': 0, 'win_rate': 0,
            'avg_return': 0, 'total_return': 0,
            'max_profit': 0, 'max_loss': 0,
        }

    rets       = [t['return_pct'] for t in trades]
    wins       = [r for r in rets if r > 0]
    win_rate   = round(len(wins) / len(trades) * 100, 1)
    avg_return = round(sum(rets) / len(rets), 2)
    total_ret  = round(equity - 100, 2)
    max_profit = round(max(rets), 2)
    max_loss   = round(min(rets), 2)

    # equity_curve 시작점 추가
    start_date = str(primary_df['date'].iloc[start_idx])[:10]
    equity_curve = [{'date': start_date, 'equity': 100}] + equity_curve

    return {
        'trades':       trades[-20:],    # 최근 20건만 반환
        'equity_curve': equity_curve,
        'total_trades': len(trades),
        'win_rate':     win_rate,
        'avg_return':   avg_return,
        'total_return': total_ret,
        'max_profit':   max_profit,
        'max_loss':     max_loss,
    }
