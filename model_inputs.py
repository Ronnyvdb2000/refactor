"""Input integrity checks shared by model scorers; no network or storage access."""
import numpy as np
import pandas as pd


class InputUnavailable(RuntimeError):
    pass


def canonical_selecties_sql():
    # Same default source choice as the forward-return builder: first recorded
    # non-null quote per ticker/date/strategy. All-null groups remain visible
    # to scoring, so missing inputs cannot disappear from coverage counts.
    return """
        WITH s AS (
            SELECT DISTINCT ON (ticker, datum, strategie) *
            FROM selecties
            ORDER BY ticker, datum, strategie, (koers IS NULL) ASC,
                     created_at ASC NULLS LAST, id ASC
        )
    """


def local_day(value):
    try:
        day = pd.Timestamp(value)
        if pd.isna(day):
            return pd.NaT
        return day.tz_localize(None).normalize() if day.tzinfo is not None else day.normalize()
    except (TypeError, ValueError):
        return pd.NaT


def technical_day(value):
    """Weekday selection: same date; Saturday/Sunday run: preceding Friday.

    This deliberately is not a generic as-of search or holiday calendar.
    """
    day = local_day(value)
    if pd.isna(day):
        return pd.NaT
    return day - pd.Timedelta(days=max(0, day.weekday() - 4))


def technical_day_sql(selection="s.datum"):
    # Internal SQL expression, shared so retrieval and validation use one policy.
    day = f"({selection})::date"
    return (f"({day} - CASE EXTRACT(ISODOW FROM {day})::int "
            "WHEN 6 THEN 1 WHEN 7 THEN 2 ELSE 0 END)")


def dates(values):
    return pd.to_datetime(values.map(local_day))


def score_date(value, *, today=None, max_age=7, historical=False):
    day = local_day(value)
    now = local_day(today if today is not None else pd.Timestamp.now(tz="Europe/Brussels"))
    if pd.isna(day):
        raise InputUnavailable("Ongeldige scoredatum")
    age = (now - day).days
    if age < 0 or (not historical and age > max_age):
        raise InputUnavailable(f"Scoredatum {day.date()} is toekomstig of verouderd (leeftijd {age} dagen)")
    return day


def numeric_features(frame, features):
    missing = set(features) - set(frame.columns)
    if missing:
        raise InputUnavailable('Featurekolommen ontbreken: ' + ', '.join(sorted(missing)))
    out = frame.copy()
    for name in features:
        out[name] = pd.to_numeric(out[name], errors='coerce').replace([np.inf, -np.inf], np.nan)
    return out


def unique_snapshots(frame, keys, values):
    """Exact repeats give no extra weight; conflicting snapshots require review."""
    out = frame.drop_duplicates(keys + values).copy()
    if out.duplicated(keys, keep=False).any():
        raise InputUnavailable('Tegenstrijdige snapshots voor dezelfde model-invoersleutel')
    return out.reset_index(drop=True)


def scoring_inputs(frame, features, *, date_column, min_rows=1, min_coverage=.8,
                   max_age=7, historical=False, today=None, rank_features=False):
    if not 0 < min_coverage <= 1 or min_rows < 1:
        raise ValueError('Ongeldige minimumdekking of minimumrijen')
    if frame.empty:
        raise InputUnavailable('Geen modelinvoer')
    out = numeric_features(frame, features)
    required = ['ticker', date_column, 'technische_datum']
    if any(name not in out for name in required):
        raise InputUnavailable('Ticker/scoredatum/technische datum ontbreekt')
    out['ticker'] = out['ticker'].fillna('').astype(str).str.strip()
    out[date_column] = dates(out[date_column])
    out['technische_datum'] = dates(out['technische_datum'])
    if out[date_column].isna().any() or out.ticker.eq('').any():
        raise InputUnavailable('Ongeldige ticker of selectiedatum in modelinvoer')
    keys = ['ticker', date_column] + (['strategie'] if 'strategie' in out else [])
    out = unique_snapshots(out, keys, list(features) + ['technische_datum'])
    latest = out[date_column].max()
    score_date(latest, today=today, max_age=max_age, historical=historical)
    expected = dates(out[date_column].map(technical_day))
    valid = out[features].notna().all(axis=1) & out['technische_datum'].eq(expected)
    if not historical:
        now = local_day(today if today is not None else pd.Timestamp.now(tz='Europe/Brussels'))
        valid &= (now - out['technische_datum']).dt.days.between(0, max_age)
    ranked = out.copy()
    if rank_features:
        for name in features:
            ranked[name] = out.groupby(date_column)[name].rank(pct=True)
    accepted, coverage = [], []
    for day, group in out.groupby(date_column, sort=True):
        usable = group.loc[valid.loc[group.index]]
        ratio = len(usable) / len(group)
        feature_day = technical_day(day).date().isoformat()
        coverage.append(dict(datum=day.date().isoformat(), technische_datum=feature_day,
                             total=len(group), usable=len(usable), ratio=ratio,
                             weekend=day.weekday() >= 5))
        if len(usable) < min_rows or ratio < min_coverage:
            message = f'{day.date()}: {len(usable)}/{len(group)} complete snapshots voor koersdatum {feature_day} ({ratio:.1%}); vereist {min_coverage:.0%} en {min_rows} rij(en)'
            if day == latest:
                raise InputUnavailable(message)
            print('[invoer] oudere datum overgeslagen: ' + message, flush=True)
            continue
        accepted.append(ranked.loc[usable.index])
    return pd.concat(accepted, ignore_index=True), coverage
