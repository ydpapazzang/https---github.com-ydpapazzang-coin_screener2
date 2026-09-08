"""Display helpers for KRW market prices."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template


register = template.Library()


@register.filter
def krw_price(value):
    """Format a price using the decimal precision appropriate to its KRW range.

    Small-priced coins must retain their actionable fractional price.  The
    precision matches the KRW market tick bands around each displayed value.
    """
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

    quantum = Decimal('1').scaleb(-places)
    rounded = price.quantize(quantum, rounding=ROUND_HALF_UP)
    return f'{rounded:,.{places}f}'
