from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .position_sizing import calculate_position_size


class PositionSizingFormulaTestCase(SimpleTestCase):
    def test_example_uses_fixed_risk_budget(self):
        result = calculate_position_size(10_000_000, 0.5, 100, 95)

        self.assertEqual(result['risk_budget'], 50_000)
        self.assertAlmostEqual(result['order_amount'], 1_000_000)
        self.assertAlmostEqual(result['quantity'], 10_000)
        self.assertAlmostEqual(result['expected_loss'], 50_000)
        self.assertFalse(result['capped_by_assets'])

    def test_order_is_capped_at_total_assets(self):
        result = calculate_position_size(1_000_000, 1, 100, 99.5)

        self.assertEqual(result['order_amount'], 1_000_000)
        self.assertTrue(result['capped_by_assets'])
        self.assertAlmostEqual(result['expected_loss'], 5_000)

    def test_stop_must_be_below_entry(self):
        with self.assertRaisesRegex(ValueError, '손절가는 진입가보다 낮아야'):
            calculate_position_size(1_000_000, 1, 100, 100)

    def test_non_finite_or_non_positive_values_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_position_size('nan', 1, 100, 95)
        with self.assertRaises(ValueError):
            calculate_position_size(1_000_000, 0, 100, 95)


class PositionSizingViewTestCase(TestCase):
    def test_calculator_displays_result(self):
        response = self.client.get(reverse('position_size_calculator'), {
            'total_assets': '10000000', 'risk_pct': '0.5',
            'entry_price': '100', 'stop_loss': '95', 'calculate': '1',
        })

        self.assertContains(response, '1,000,000원')
        self.assertContains(response, '50,000원')
        self.assertContains(response, '10000.00000000')

    def test_invalid_prices_show_error_without_server_error(self):
        response = self.client.get(reverse('position_size_calculator'), {
            'total_assets': '10000000', 'risk_pct': '0.5',
            'entry_price': '100', 'stop_loss': '101', 'calculate': '1',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '손절가는 진입가보다 낮아야')

