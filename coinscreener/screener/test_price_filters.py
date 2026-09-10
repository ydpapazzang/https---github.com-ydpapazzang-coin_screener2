from django.test import SimpleTestCase

from .price_format import format_krw_price
from .templatetags.price_filters import krw_price


class KrwPriceFilterTestCase(SimpleTestCase):
    def test_keeps_three_decimals_for_one_to_ten_krw_prices(self):
        self.assertEqual(krw_price(6.07), '6.070')
        self.assertEqual(krw_price(6.07607), '6.076')
        self.assertEqual(format_krw_price(91.70), '91.70')

    def test_uses_larger_price_tick_precision(self):
        self.assertEqual(krw_price(7.450574), '7.451')
        self.assertEqual(krw_price(661.59488), '661.6')
        self.assertEqual(krw_price(109701000), '109,701,000')
