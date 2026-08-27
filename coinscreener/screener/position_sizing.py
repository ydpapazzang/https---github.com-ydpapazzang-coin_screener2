import math


def calculate_position_size(total_assets, risk_pct, entry_price, stop_loss):
    """고정 비율 위험 예산으로 최대 현물 주문금액과 수량을 계산한다."""
    values = {
        'total_assets': total_assets,
        'risk_pct': risk_pct,
        'entry_price': entry_price,
        'stop_loss': stop_loss,
    }
    parsed = {}
    for name, raw in values.items():
        try:
            value = float(str(raw).replace(',', ''))
        except (TypeError, ValueError):
            raise ValueError('모든 항목에 숫자를 입력하세요.')
        if not math.isfinite(value) or value <= 0:
            raise ValueError('모든 항목은 0보다 커야 합니다.')
        parsed[name] = value

    if parsed['risk_pct'] > 100:
        raise ValueError('1회 허용 손실률은 100% 이하여야 합니다.')
    if parsed['stop_loss'] >= parsed['entry_price']:
        raise ValueError('손절가는 진입가보다 낮아야 합니다.')

    stop_distance = parsed['entry_price'] - parsed['stop_loss']
    stop_distance_pct = stop_distance / parsed['entry_price'] * 100
    risk_budget = parsed['total_assets'] * parsed['risk_pct'] / 100
    uncapped_order = risk_budget / (stop_distance / parsed['entry_price'])
    order_amount = min(uncapped_order, parsed['total_assets'])
    quantity = order_amount / parsed['entry_price']
    expected_loss = quantity * stop_distance

    return {
        **parsed,
        'risk_budget': risk_budget,
        'stop_distance': stop_distance,
        'stop_distance_pct': stop_distance_pct,
        'uncapped_order_amount': uncapped_order,
        'order_amount': order_amount,
        'quantity': quantity,
        'expected_loss': expected_loss,
        'capital_usage_pct': order_amount / parsed['total_assets'] * 100,
        'capped_by_assets': uncapped_order > parsed['total_assets'],
    }

