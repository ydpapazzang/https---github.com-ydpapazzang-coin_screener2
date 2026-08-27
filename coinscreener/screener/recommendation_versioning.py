import math
import subprocess
from functools import lru_cache

from django.conf import settings


DANTA_STRATEGY_VERSION = 'danta-breakout-v2.0'
SWING_STRATEGY_VERSION = 'swing-trend-v1.0'


@lru_cache(maxsize=1)
def current_code_version():
    """Return an immutable deploy identifier without making generation fragile."""
    configured = getattr(settings, 'APP_COMMIT_SHA', '').strip()
    if configured:
        return configured[:64]

    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=settings.BASE_DIR,
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        )
        commit = result.stdout.strip()
        return commit[:64] if commit else 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def json_number(value):
    """Convert numpy/pandas numeric values to JSON-safe finite Python values."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def recommendation_snapshot(strategy_version, parameters, market_regime, data_as_of):
    return {
        'strategy_version': strategy_version,
        'strategy_parameters': parameters,
        'market_regime': market_regime,
        'data_as_of': data_as_of,
        'code_version': current_code_version(),
    }

