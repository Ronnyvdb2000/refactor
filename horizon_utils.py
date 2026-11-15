"""Trading-session horizons and conservative calendar separation for labels/models.

Calendar estimates select candidate rows and separate train/test windows. They
are not an exchange calendar: label creation must still verify actual sessions.
"""
import math

SUPPORTED_HORIZONS = (5, 10, 20, 30, 60)


def horizon_days(horizon):
    value = str(horizon).strip().lower()
    if value.endswith('d'):
        value = value[:-1]
    if not value.isdigit() or int(value) not in SUPPORTED_HORIZONS:
        raise ValueError(f'Ongeldige horizon {horizon!r}; toegestaan: {SUPPORTED_HORIZONS}')
    return int(value)


def calendar_embargo(horizon, minimum=0):
    """Approximate trading days as calendar days, plus a two-day buffer."""
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
        raise ValueError('Minimum embargo moet een niet-negatief geheel getal zijn')
    return max(minimum, math.ceil(horizon_days(horizon) * 7 / 5) + 2)
