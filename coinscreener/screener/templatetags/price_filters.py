"""Django template filters for KRW market prices."""

from django import template

from ..price_format import format_krw_price


register = template.Library()


@register.filter
def krw_price(value):
    return format_krw_price(value)
