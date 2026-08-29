from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd
from django.test import TestCase

from .backtest import _check_conditions_at, _prepare_frame
from .engine import check_strategy, get_indicator_value, get_required_len
from .models import Condition, Strategy


def price_frame(closes):
    dates = [datetime(2025, 1, 1) + timedelta(days=index) for index in range(len(closes))]
    return pd.DataFrame({
        'open': closes, 'high': [value + 2 for value in closes],
        'low': [value - 2 for value in closes], 'close': closes,
        'volume': [1000.0] * len(closes), 'value': [100000.0] * len(closes),
    }, index=dates)


class IchimokuTriplePresetTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name='내 전략', owner_key='mine')
        session = self.client.session
        session['owner_key'] = 'mine'
        session.save()

    def test_preset_creates_six_repeat_safe_conditions(self):
        url = f'/strategy/{self.strategy.id}/preset/ichimoku-triple/'
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.strategy.conditions.count(), 6)
        self.client.post(url)
        self.assertEqual(self.strategy.conditions.count(), 6)

        conditions = list(self.strategy.conditions.all())
        self.assertTrue(all(condition.closed_only for condition in conditions))
        cloud = self.strategy.conditions.get(
            left_indicator='CLOSE', right_indicator='IC_CLOUD_TOP'
        )
        self.assertEqual(cloud.threshold_pct, 1.0)
        volume = self.strategy.conditions.get(right_indicator='VOLUME_MA')
        self.assertEqual(volume.right_param, 20)
        self.assertEqual(volume.bb_std, 1.2)
        self.assertTrue(
            self.strategy.conditions.filter(right_indicator='IC_PAST_CLOUD').exists()
        )

    def test_preset_uses_selected_timeframe_and_period_window(self):
        url = f'/strategy/{self.strategy.id}/preset/ichimoku-triple/'
        response = self.client.post(url, {'timeframe': 'week', 'offset': '3'})

        self.assertEqual(response.status_code, 302)
        conditions = self.strategy.conditions.all()
        self.assertEqual(conditions.count(), 6)
        self.assertTrue(all(condition.timeframe == 'week' for condition in conditions))
        self.assertTrue(all(condition.offset == 3 for condition in conditions))

    def test_preset_invalid_timeframe_and_period_use_safe_defaults(self):
        url = f'/strategy/{self.strategy.id}/preset/ichimoku-triple/'
        self.client.post(url, {'timeframe': 'hour', 'offset': '999'})

        conditions = self.strategy.conditions.all()
        self.assertTrue(all(condition.timeframe == 'day' for condition in conditions))
        self.assertTrue(all(condition.offset == 0 for condition in conditions))


class IchimokuConditionEngineTestCase(TestCase):
    def setUp(self):
        self.strategy = Strategy.objects.create(name='engine')

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_closed_only_ignores_live_candle(self, get_ohlcv):
        closes = [100.0] * 118 + [100.0, 200.0]
        get_ohlcv.return_value = price_frame(closes)
        condition = Condition.objects.create(
            strategy=self.strategy, timeframe='day', closed_only=True,
            left_indicator='CLOSE', left_param=0, operator='gt',
            right_indicator='VAL', right_param=150,
        )
        self.assertFalse(check_strategy('KRW-BTC', [condition])[0])
        condition.closed_only = False
        condition.save(update_fields=['closed_only'])
        self.assertTrue(check_strategy('KRW-BTC', [condition])[0])

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_threshold_requires_distance_above_reference(self, get_ohlcv):
        closes = [105.0] * 120
        get_ohlcv.return_value = price_frame(closes)
        condition = Condition.objects.create(
            strategy=self.strategy, timeframe='day', threshold_pct=10,
            left_indicator='CLOSE', left_param=0, operator='gte',
            right_indicator='VAL', right_param=100,
        )
        self.assertFalse(check_strategy('KRW-BTC', [condition])[0])
        get_ohlcv.return_value = price_frame([115.0] * 120)
        self.assertTrue(check_strategy('KRW-BTC', [condition])[0])

    @patch('coinscreener.screener.engine.get_ohlcv_with_retry')
    def test_n_bar_window_matches_realtime_and_backtest(self, get_ohlcv):
        closes = [90.0] * 117 + [90.0, 110.0, 110.0]
        frame = price_frame(closes)
        get_ohlcv.return_value = frame
        condition = Condition.objects.create(
            strategy=self.strategy, timeframe='day', offset=2,
            left_indicator='CLOSE', left_param=0, operator='cross_up',
            right_indicator='VAL', right_param=100,
        )
        self.assertTrue(check_strategy('KRW-BTC', [condition])[0])
        prepared = _prepare_frame(frame, 'day')
        signal_time = prepared['_available_at'].iloc[-1]
        self.assertTrue(_check_conditions_at({'day': prepared}, [condition], signal_time))

    def test_past_cloud_indicator_has_required_history_and_value(self):
        frame = price_frame([100.0 + index for index in range(130)])
        self.assertEqual(get_required_len('IC_PAST_CLOUD', 52), 104)
        value = get_indicator_value(frame, 'IC_PAST_CLOUD', 52, 0)
        self.assertIsNotNone(value)

