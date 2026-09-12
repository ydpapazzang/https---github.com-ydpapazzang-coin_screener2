from types import SimpleNamespace

from django.test import SimpleTestCase

from .danta_dual_timeframe import (
    AGGRESSIVE_DANTA_STRATEGY_VERSION,
    DUAL_DANTA_STRATEGY_VERSION,
)
from .management.commands.update_upbit_cache import Command


class DualTimeframeDantaProfileTestCase(SimpleTestCase):
    def test_both_danta_profiles_use_dual_timeframe_tracking(self):
        for strategy_version in (
            DUAL_DANTA_STRATEGY_VERSION,
            AGGRESSIVE_DANTA_STRATEGY_VERSION,
        ):
            record = SimpleNamespace(strategy_version=strategy_version)
            self.assertTrue(Command._is_dual_timeframe_danta(record))

        legacy_record = SimpleNamespace(strategy_version='danta-volume-spike-v1')
        self.assertFalse(Command._is_dual_timeframe_danta(legacy_record))

    def test_aggressive_profile_uses_its_saved_partial_exit_fraction(self):
        record = SimpleNamespace(strategy_parameters={'first_take_profit_fraction': 0.7})
        self.assertAlmostEqual(Command._partial_exit_fraction(record), 0.7)
