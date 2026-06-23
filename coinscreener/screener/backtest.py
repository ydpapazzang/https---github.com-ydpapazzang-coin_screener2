"""
백테스팅 엔진
- 매수: 해당 봉에서 전략 조건 충족 시 진입
- 매도: 3가지 모드 (N봉 후 / 익절·손절 % / 조건 이탈 시) asfsafdf
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


def _check_conditions_at(df_map, conditions, idx: int) -> bool:
    """df_map의 idx 시점에서 조건 충족 여부 확인 (cond.offset 반영)"""
    for cond in conditions:
        df = df_map.get(cond.timeframe)
        if df is None or idx >= len(df):
            return False

        # 백테스팅 시점 idx 기준으로 cond.offset(n봉 전)을 적용하여 offset을 구합니다.
        # (len(df) - 1) - idx 는 현재 백테스팅 대상 봉(idx)의 최신 기준 오프셋이며,
        # 여기에 cond.offset을 더해주면 idx 기준으로 지정된 과거 봉을 참조하게 됩니다.
        offset = (len(df) - 1) - idx + cond.offset

        # ── 하이킨아시 패턴 조건 처리 ──
        ha_patterns = ('HA_BULL', 'HA_BEAR', 'HA_BULL_N', 'HA_BEAR_N', 'HA_NO_LOWER', 'HA_NO_UPPER')
        if cond.left_indicator in ha_patterns:
            if not check_ha_pattern(df, cond.left_indicator, cond.left_param, offset):
                return False
            continue  # 조건 만족, 다음 조건으로

        # ── 일반 지표 조건 처리 ──
        bb_std = cond.bb_std if cond.bb_std is not None else 2.0
        lv = get_indicator_value(df, cond.left_indicator,  cond.left_param,  offset, bb_std=bb_std)
        rv = get_indicator_value(df, cond.right_indicator, cond.right_param, offset, bb_std=bb_std)
        if lv is None or rv is None:
            return False
        ops = {'gt': lv>rv, 'lt': lv<rv, 'gte': lv>=rv, 'lte': lv<=rv}
        if not ops.get(cond.operator, False):
            return False
    return True


def run_backtest(ticker: str, conditions: list, candle_count: int,
                 sell_mode: str, sell_param: float, fee_pct: float = 0.05) -> dict:
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
                # 수익률 계산 시 매매 수수료(fee_pct)를 양방향(매수, 매도)으로 적용
                fee_ratio = fee_pct / 100.0
                gross_ratio = price / entry_price
                # (1 - fee_ratio)가 두 번 곱해지는 것은 매수/매도 시 각각 수수료가 발생하기 때문
                net_ratio = gross_ratio * ((1 - fee_ratio) ** 2)
                ret = (net_ratio - 1) * 100

                # 자산(equity)은 순수익률(net_ratio)을 곱하여 업데이트
                equity = equity * net_ratio
                trades.append({
                    'entry_date':  entry_date,
                    'exit_date':   date,
                    'entry_price': entry_price,
                    'exit_price':  price,
                    'return_pct':  round(ret, 2),
                })
                in_position = False
                equity_curve.append({'date': date, 'equity': round(equity, 4)})

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

    # 샤프 비율 (Sharpe Ratio) 계산
    std_ret = np.std(rets, ddof=1) if len(rets) > 1 else 0.0
    sharpe = round(np.mean(rets) / std_ret, 2) if std_ret > 0.0 else 0.0

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
    }
