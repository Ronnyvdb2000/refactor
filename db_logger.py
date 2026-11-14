"""
db_logger.py
------------
Herbruikbare module om bot-selecties naar de Supabase-tabel `selecties`
te loggen, naast de bestaande Telegram/e-mail-notificaties.

Gebruik in een bot:

    from db_logger import log_selectie

    log_selectie(
        ticker="AAPL",
        datum="2026-08-04",
        strategie="bot_00kr",
        beurs="NASDAQ",
        koers=231.45,
        parameters={"rsi_period": 14, "threshold": 0.7},
    )

Vereist:
- pip install psycopg2-binary
- GitHub Actions secret SUPABASE_DB_URL (Connect -> Direct Connection ->
  Session/Transaction pooler-string, met wachtwoord ingevuld).
  Gebruik de POOLER-variant, niet "Direct connection": GitHub Actions-
  runners hebben geen IPv6, en de Direct Connection-hostname is enkel
  over IPv6 bereikbaar.

Herstarts en gelijktijdige runs:
- Dezelfde ticker/datum/strategie/beurs/horizon wordt niet opnieuw ingevoegd.
- Een transactiegebonden advisory lock serialiseert schrijvers die deze helper gebruiken.
- Bestaande geldige snapshots blijven behouden; een ontbrekende koers kan worden aangevuld.
- Dit vervangt geen database-UNIQUE-constraint voor andere, rechtstreekse schrijvers.
- Tijdelijke verbindingsfouten blokkeren niet langer alle volgende aanroepen.

2026-08-17: _KOLOM_WHITELIST uitgebreid met de weekdag/maand/kwartaal-
kolommen van bot_01repititief.py (seizoenseffecten-bot). Vereist dat de
bijhorende ALTER TABLE-migratie is uitgevoerd op Supabase, zie
migratie_seizoenskolommen.sql.

2026-08-18: _KOLOM_WHITELIST uitgebreid met de kolommen van bot_01kasstr.py
(kasstroom onderwaardering). Vereist de bijhorende ALTER TABLE-migratie,
zie migratie_kasstr_kolommen.sql.

2026-08-19: _KOLOM_WHITELIST uitgebreid met de kolommen van bot_01xgboost.py
(XGBoost richtingsvoorspelling). Vereist de bijhorende ALTER TABLE-migratie,
zie migratie_xgboost_kolommen.sql.

2026-08-22: _KOLOM_WHITELIST uitgebreid met de kolommen van bot_01hoogl.py
(GARP onderwaardering). Vereist de bijhorende ALTER TABLE-migratie, zie
migratie_hoogl_kolommen.sql.

2026-08-23: _KOLOM_WHITELIST uitgebreid met market_cap en analisten_count,
gedeeld door bot_01kasstr.py en bot_01hoogl.py (nieuwe marktkap/analist-
coverage criteria, BeursBrink-stijl). Vereist de bijhorende ALTER TABLE-
migratie, zie migratie_marktkap_analisten_kolommen.sql.

2026-08-23: _KOLOM_WHITELIST uitgebreid met piotroski_score (Piotroski
F-Score, 0-9, informatief) in bot_01kasstr.py. Vereist de bijhorende
ALTER TABLE-migratie, zie migratie_piotroski_kolom.sql. De volledige
per-criterium breakdown (piotroski_detail) is NIET gewhitelist en blijft
enkel in de JSON parameters-kolom staan.

2026-09-21: _KOLOM_WHITELIST uitgebreid met combined_rank, roc_rank,
ey_rank (bot_01greenblatt.py, Magic Formula) en vc2_score
(bot_01oshaughnessy.py, Trending Value). Deze 4 kolommen bestonden al in
de `selecties`-tabel (geen nieuwe migratie nodig) maar stonden niet in de
whitelist, waardoor ze bij elke insert enkel in de JSON parameters-kolom
belandden en de wide columns altijd NULL bleven. Ontdekt via
weekly_db_opvolg.py's haal_laatste_rank_scores(), die deze wide columns
leest en daardoor voor alle 4 velden n=0 opleverde in de
weekly-correlatie-analyse.
"""

import os
import json
import math
import logging
from datetime import date, datetime

import psycopg2
from psycopg2 import sql

logger = logging.getLogger(__name__)

_DB_URL_ENV = "SUPABASE_DB_URL"

# Als de DSN zelf ongeldig blijkt (config-fout), zetten we dit op True
# zodat we niet bij elke ticker dezelfde fout opnieuw proberen/loggen.
_dsn_invalid = False

# Kolommen die daadwerkelijk als aparte kolom in `selecties` bestaan
# (ALTER TABLE al uitgevoerd op 2026-08-08, uitgebreid op 2026-08-17). Enkel
# keys uit `parameters` die hierin voorkomen worden als losse kolom
# meegeschreven; de rest blijft (ook) gewoon in de `parameters`-JSON-kolom
# staan. Zo faalt een insert nooit doordat een bot een key gebruikt die nog
# geen kolom heeft.
_KOLOM_WHITELIST = {
    "score", "total_score", "grafiek",
    # gedeeld door bot_01kasstr en bot_01hoogl (marktkap/analist-coverage criteria) -- toegevoegd 2026-08-23
    "market_cap", "analisten_count",
    # bot_00kr
    "rsi_monthly", "rsi_label", "macd_label", "rr_pct",
    "resistance", "support", "div_yield", "atr", "stop",
    # bot_00ms / bot_00cs / bot_00dm
    "rs", "pivot", "pct_from_high", "pct_from_low",
    # bot_00db / bot_00vcp
    "n_boxes", "box_top", "box_bottom", "box_pct",
    "n_contracties", "laatste_pct", "breakout", "breakout_vol", "stage2",
    # bot_00cs
    "eps_q_growth_pct", "eps_annual_cagr_pct", "inst_pct", "ma200", "high52w",
    # bot_01repititief (seizoenseffecten) -- toegevoegd 2026-08-17
    "jaren_data",
    "weekdag_gemiddelde", "weekdag_winrate", "weekdag_p",
    "maand_gemiddelde", "maand_winrate", "maand_p",
    "kwartaal_gemiddelde", "kwartaal_winrate", "kwartaal_p",
    # bot_01kasstr (kasstroom onderwaardering) -- toegevoegd 2026-08-18
    "rank",
    "fcf_yield", "fcf_years", "fcf_growing", "fcf_consistent",
    "rev_growth_pct", "net_debt_ebitda", "payout_pct",
    # bot_01xgboost (XGBoost richtingsvoorspelling) -- toegevoegd 2026-08-19
    "proba", "rsi", "macd_hist", "macd_hist_slope",
    "dist_sma50_pct", "dist_sma200_pct",
    "atr_pct", "vol_ratio_20d", "ret_5d", "ret_20d", "ret_60d",
    "horizon_days",
    # bot_01xgboost -- markt_ret_5d toegevoegd 2026-08-20 (marktcontext-feature)
    "markt_ret_5d",
    # bot_01hoogl (GARP onderwaardering) -- toegevoegd 2026-08-22
    "roe_pct", "terugverdienperiode", "forward_pe", "verwachte_winstgroei_pct",
    # bot_01kasstr (Piotroski F-Score, informatief) -- toegevoegd 2026-08-23
    "piotroski_score",
    # bot_01greenblatt (Magic Formula) / bot_01oshaughnessy (Trending Value)
    # -- ontbrak, ontdekt 2026-09-21 via lege niveau_-kolommen in de
    # weekly-correlatie-analyse (zie moduledocstring hierboven)
    "combined_rank", "roc_rank", "ey_rank", "vc2_score",
}


def _sanitize(value):
    """
    Vervangt NaN/Infinity door None (overal, ook genest in dicts/lijsten).
    Nodig omdat Python's json.dumps NaN/Infinity schrijft als de letterlijke
    tokens NaN/Infinity, wat GEEN geldige JSON is -- Postgres' jsonb-type
    weigert dat met 'invalid input syntax for type json'.
    """
    # NumPy/Pandas scalars must become native Python values before both
    # JSON encoding and psycopg2 parameter binding (e.g. np.float64).
    if hasattr(value, "item") and not isinstance(value, (str, bytes, dict, list, tuple)):
        try:
            native = value.item()
            if native is not value:
                return _sanitize(native)
        except (AttributeError, TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def _get_connection():
    db_url = os.environ.get(_DB_URL_ENV, "")
    # .strip() vangt de meest voorkomende oorzaak van "invalid dsn":
    # een per ongeluk meegekopieerde spatie/tab/regeleinde in de secret.
    db_url = db_url.strip().strip('"').strip("'")
    if not db_url:
        raise RuntimeError(
            f"Omgevingsvariabele {_DB_URL_ENV} ontbreekt. "
            f"Voeg ze toe als GitHub Actions secret en geef ze door aan de workflow."
        )
    if not (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
        raise RuntimeError(
            f"{_DB_URL_ENV} lijkt geen geldige connectiestring: moet beginnen "
            f"met 'postgresql://' of 'postgres://'. Controleer op een per "
            f"ongeluk meegekopieerde spatie, aanhalingsteken of regeleinde."
        )
    return psycopg2.connect(db_url, connect_timeout=15)


def _bouw_insert(parameters: dict):
    """
    Bouwt de kolomlijst/placeholders/waarden voor de losse parameter-kolommen,
    op basis van welke keys uit `parameters` in _KOLOM_WHITELIST voorkomen.
    Keys die niet in de whitelist staan, komen enkel in de `parameters`-JSON
    terecht (geen aparte kolom voor -> zou de insert laten falen).
    """
    if not parameters:
        return [], []
    kolommen = [k for k in parameters.keys() if k in _KOLOM_WHITELIST]
    waarden = [parameters[k] for k in kolommen]
    return kolommen, waarden


def _prepare_row(row):
    row = dict(row)
    datum = row.get("datum")
    if isinstance(datum, datetime):
        datum = datum.date()
    if isinstance(datum, date):
        datum = datum.isoformat()
    # The natural key uses a calendar date, never a run timestamp.
    date.fromisoformat(datum)
    for key in ("ticker", "strategie"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"{key} ontbreekt")
    row["datum"] = datum
    row["parameters"] = _sanitize(row.get("parameters"))
    if row["parameters"] is not None and not isinstance(row["parameters"], dict):
        raise ValueError("parameters moet een dict zijn")
    row["koers"] = _sanitize(row.get("koers"))
    if row["koers"] is not None:
        row["koers"] = float(row["koers"])
        if not math.isfinite(row["koers"]) or row["koers"] <= 0:
            raise ValueError("koers moet positief en eindig zijn")
    return row


def _selectie_key(row):
    parameters = row["parameters"] or {}
    # ->> in PostgreSQL returns strings for JSON scalar values too.
    def json_text(key):
        value = parameters.get(key)
        if value is None:
            return None
        return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    horizon, horizon_days = json_text("horizon"), json_text("horizon_days")
    return [row["ticker"], row["datum"], row["strategie"], row.get("beurs"), horizon, horizon_days]


def _write_selectie(cur, row):
    """Insert once per ticker/date/strategy/exchange/horizon, under a key lock.

    Returns True for an insert or missing-price repair, False for an already
    stored selection. Existing valid snapshots are never replaced on a rerun.
    All writers must use this protocol; it is not a database unique constraint.
    """
    row = _prepare_row(row)
    key = _selectie_key(row)
    # Separate statements are essential: under READ COMMITTED the SELECT
    # after waiting for a competing writer observes its newly committed row.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (json.dumps(key, ensure_ascii=True, separators=(",", ":")),))
    cur.execute("""
        SELECT id, koers FROM selecties
        WHERE ticker = %s AND datum = %s AND strategie = %s
          AND beurs IS NOT DISTINCT FROM %s
          AND parameters->>'horizon' IS NOT DISTINCT FROM %s
          AND parameters->>'horizon_days' IS NOT DISTINCT FROM %s
        ORDER BY (koers > 0 AND koers < 'Infinity'::real) DESC NULLS LAST,
                 created_at ASC NULLS LAST, id ASC
        LIMIT 1
    """, tuple(key))
    existing = cur.fetchone()
    if existing:
        old_price = existing[1]
        if row["koers"] is not None and (old_price is None or not math.isfinite(float(old_price)) or old_price <= 0):
            cur.execute("UPDATE selecties SET koers = %s WHERE id = %s", (row["koers"], existing[0]))
            return True
        return False
    extras, extra_values = _bouw_insert(row["parameters"])
    columns = ["ticker", "datum", "strategie", "beurs", "koers", "parameters"] + extras
    values = [row["ticker"], row["datum"], row["strategie"], row.get("beurs"), row["koers"],
              json.dumps(row["parameters"], allow_nan=False) if row["parameters"] is not None else None] + extra_values
    cur.execute(sql.SQL("INSERT INTO selecties ({}) VALUES ({})").format(
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in values)), values)
    return True


def log_selectie(ticker: str, datum, strategie: str, beurs: str = None,
                 koers: float = None, parameters: dict = None) -> bool:
    """True means persisted or already present; reruns preserve valid snapshots."""
    global _dsn_invalid
    if _dsn_invalid:
        return False
    try:
        row = _prepare_row(dict(ticker=ticker, datum=datum, strategie=strategie,
                                beurs=beurs, koers=koers, parameters=parameters))
    except (ValueError, TypeError) as exc:
        logger.error("db_logger: ongeldige selectie %s: %s", ticker, exc)
        return False
    try:
        conn = _get_connection()
    except Exception as exc:
        logger.error("db_logger: kon geen DB-connectie opzetten: %s", exc)
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '15s'")
            _write_selectie(cur, row)
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        logger.error("db_logger: opslag voor %s (%s) mislukt: %s", ticker, strategie, exc)
        return False
    finally:
        conn.close()


def log_selecties_bulk(rows: list, connection=None) -> int:
    """Returns inserted/repaired count; existing valid selections count as zero.

    Optional connection is committed/rolled back here, but never closed here.
    Savepoints isolate row failures; counts are returned only after commit.
    Sort keys to keep advisory lock acquisition deterministic across batches.
    """
    if not rows or _dsn_invalid:
        return 0
    own_connection = connection is None
    try:
        conn = _get_connection() if own_connection else connection
    except Exception as exc:
        logger.error("db_logger: kon geen DB-connectie opzetten: %s", exc)
        return 0
    count = 0
    try:
        prepared = []
        for row in rows:
            try:
                prepared.append(_prepare_row(row))
            except (ValueError, TypeError) as exc:
                logger.error("db_logger: ongeldige rij (%s): %s", row.get("ticker"), exc)
        def order(row):
            return json.dumps(_selectie_key(row), ensure_ascii=True, separators=(",", ":"))
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '15s'")
            for row in sorted(prepared, key=order):
                cur.execute("SAVEPOINT selectie_row")
                try:
                    changed = _write_selectie(cur, row)
                    cur.execute("RELEASE SAVEPOINT selectie_row")
                    count += int(changed)
                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT selectie_row")
                    cur.execute("RELEASE SAVEPOINT selectie_row")
                    logger.error("db_logger: rij overgeslagen (%s): %s", row["ticker"], exc)
        conn.commit()
        return count
    except Exception as exc:
        conn.rollback()
        logger.error("db_logger: bulkopslag mislukt: %s", exc)
        return 0
    finally:
        if own_connection:
            conn.close()
