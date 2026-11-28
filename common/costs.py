"""Cost units shared by portfolio execution and descriptive score studies.

Execution rates are per side. A fixed round-trip signal deduction is a separate
scenario, not a substitute for commissions/slippage on actual traded notionals.
Frozen configurations, including overrides, always take precedence over defaults.
"""
import math

COMMISSION_BPS_PER_SIDE = 5
SLIPPAGE_BPS_PER_SIDE = 10
FX_BPS_PER_SIDE = 5
SIGNAL_ROUND_TRIP_BPS = 20
GROSS_SIGNAL_ROUND_TRIP_BPS = 0
ROUND_TRIP_SENSITIVITY_BPS = (0, 10, 20, 40)


def bps_fraction(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Kosten moeten eindig en niet negatief zijn')
    return value / 10000


def execution_defaults():
    return dict(commission_bps=COMMISSION_BPS_PER_SIDE,
                slippage_bps=SLIPPAGE_BPS_PER_SIDE)


def execution_rates(config):
    return bps_fraction(config['commission_bps']), bps_fraction(config['slippage_bps'])


def net_signal_pct(gross_pct, round_trip_bps):
    # gross_pct is in percentage points, costs are basis points.
    return gross_pct - bps_fraction(round_trip_bps) * 100
