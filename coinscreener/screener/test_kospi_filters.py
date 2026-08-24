from django.test import SimpleTestCase

from .kospi_filters import (
    filter_kospi_products,
    is_kospi_cash_management_product,
)


class KospiCashManagementFilterTestCase(SimpleTestCase):
    def test_cash_management_products_are_excluded(self):
        excluded_names = (
            "KODEX 머니마켓액티브",
            "KODEX CD금리액티브(합성)",
            "TIGER CD금리투자KIS(합성)",
            "KODEX KOFR금리액티브(합성)",
            "SOL 초단기채권액티브",
            "TIGER 단기통안채",
        )

        for name in excluded_names:
            with self.subTest(name=name):
                self.assertTrue(is_kospi_cash_management_product(name))

    def test_equity_gold_and_long_bond_products_remain(self):
        included_names = (
            "KODEX 200",
            "TIGER 화장품",
            "ACE KRX금현물",
            "KODEX 골드선물(H)",
            "TIGER 미국S&P500(H)",
            "KODEX 국고채10년액티브",
        )

        for name in included_names:
            with self.subTest(name=name):
                self.assertFalse(is_kospi_cash_management_product(name))

    def test_filter_preserves_order(self):
        products = [
            {"ticker": "1", "name": "TIGER 화장품"},
            {"ticker": "2", "name": "KODEX 머니마켓액티브"},
            {"ticker": "3", "name": "ACE KRX금현물"},
        ]

        self.assertEqual(
            [item["ticker"] for item in filter_kospi_products(products)],
            ["1", "3"],
        )
