"""Consistent display formatting for KRW market prices."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def format_krw_price(value):
    """Keep the fractional precision needed for a KRW market price."""
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return '-'

    absolute = abs(price)
    if absolute >= Decimal('1000'):
        places = 0
    elif absolute >= Decimal('100'):
        places = 1
    elif absolute >= Decimal('10'):
        places = 2
    elif absolute >= Decimal('1'):
        places = 3
    elif absolute >= Decimal('0.1'):
        places = 4
    elif absolute >= Decimal('0.01'):
        places = 5
    else:
        places = 8

    rounded = price.quantize(Decimal('1').scaleb(-places), rounding=ROUND_HALF_UP)
    return f'{rounded:,.{places}f}'
