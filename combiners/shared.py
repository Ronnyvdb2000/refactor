"""Shared selection handling, comparable scores and reporting for combiners."""

import math
import os
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, timedelta
import psycopg2
import psycopg2.extras
from common import today_str, esc, yahoo_link, trade_cost

FAMILIES = {
    "bot_01beurssig_full": "bot_01beurssig",
    "bot_01hoogl_full": "bot_01hoogl",
    "bot_01graham": "bot_00graham",
    "bot_01greenblatt": "bot_00greenblatt",
}


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def family(strategy):
    # Derived output is not independent confirmation of its own inputs.
    if (
        not isinstance(strategy, str)
        or not strategy.startswith("bot_")
        or strategy.startswith("bot_combi_")
    ):
        return None
    return FAMILIES.get(strategy, strategy)


def fetch_selections(url, lookback_days):
    if not url:
        raise RuntimeError("SUPABASE_DB_URL ontbreekt")
    if lookback_days < 1:
        raise ValueError("LOOKBACK_DAYS moet positief zijn")
    end = date.fromisoformat(today_str())
    start = end - timedelta(days=lookback_days - 1)
    with psycopg2.connect(url, connect_timeout=15) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id,ticker,beurs,strategie,datum,koers,score,grafiek,parameters,created_at
                FROM selecties WHERE datum >= %s AND datum <= %s
                ORDER BY datum DESC,created_at DESC NULLS LAST,id DESC""",
                (start.isoformat(), end.isoformat()),
            )
            return cur.fetchall()


def selection_rows(rows, lookback_days):
    if lookback_days < 1:
        raise ValueError("LOOKBACK_DAYS moet positief zijn")
    end = date.fromisoformat(today_str())
    start = end - timedelta(days=lookback_days - 1)
    latest = {}
    for raw in rows:
        fam = family(raw.get("strategie"))
        ticker = raw.get("ticker")
        if (
            not fam
            or not isinstance(ticker, str)
            or not ticker.strip()
            or "/" in ticker
        ):
            continue
        try:
            d = date.fromisoformat(str(raw.get("datum")))
        except (TypeError, ValueError):
            continue
        if not start <= d <= end:
            continue
        r = dict(raw)
        r.update(strategie=fam, datum=d.isoformat())
        params = r.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        score = finite(r.get("score"))
        if score is None:
            score = finite(params.get("score"))
        r["score"] = score
        r["koers"] = finite(r.get("koers"))
        if r["koers"] is not None and r["koers"] <= 0:
            r["koers"] = None
        r["grafiek"] = yahoo_link(ticker)
        key = (ticker, r.get("beurs"), fam, r["datum"])
        # Choose deterministically. A valid price is preferred on the same day;
        # never replace a later day by an older valid quote.
        order = (
            r["koers"] is not None,
            str(r.get("created_at") or ""),
            int(r.get("id") or 0),
            repr(sorted((str(k), str(v)) for k, v in raw.items())),
        )
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, r)
    return [v[1] for v in latest.values()]


def relative_scores(rows):
    """Each strategy family receives its own scale; one latest row per asset."""
    distributions = defaultdict(list)
    for r in rows:
        if r["score"] is not None:
            distributions[r["strategie"]].append(r["score"])
    for values in distributions.values():
        values.sort()
    result = {}
    for r in rows:
        values = distributions[r["strategie"]]
        value = r["score"]
        percentile = 0.5
        if value is not None and len(values) > 1:
            percentile = (
                (bisect_left(values, value) + bisect_right(values, value) - 1) / 2
            ) / (len(values) - 1)
        result[id(r)] = percentile
    return result


def group_signals(rows, lookback_days, fresh=False):
    cleaned = selection_rows(rows, lookback_days)
    grouped = defaultdict(list)
    for r in cleaned:
        grouped[(r["ticker"], r.get("beurs"))].append(r)
    chosen = []
    totals = {}
    for key, values in grouped.items():
        totals[key] = len({r["strategie"] for r in values})
        last = max(r["datum"] for r in values)
        by_family = {}
        for r in sorted(values, key=lambda r: (r["datum"], r["strategie"])):
            if not fresh or r["datum"] == last:
                by_family[r["strategie"]] = r
        chosen.extend(by_family.values())
    scores = relative_scores(chosen)
    groups = defaultdict(list)
    for r in chosen:
        groups[(r["ticker"], r.get("beurs"))].append(r)
    result = []
    for (ticker, exchange), values in groups.items():
        last = max(r["datum"] for r in values)
        latest = [r for r in values if r["datum"] == last]
        priced = [r for r in latest if r["koers"] is not None]
        quote = (
            max(
                priced,
                key=lambda r: (
                    str(r.get("created_at") or ""),
                    int(r.get("id") or 0),
                    r["strategie"],
                ),
            )
            if priced
            else None
        )
        strategies = sorted({r["strategie"] for r in values})
        if fresh and len(strategies) < 2:
            continue
        result.append(
            dict(
                ticker=ticker,
                beurs=exchange,
                overlap=len(strategies),
                overlap_totaal=totals[(ticker, exchange)],
                strategieen=strategies,
                avg_score=sum(scores[id(r)] for r in values) / len(values),
                koers=quote["koers"] if quote else None,
                laatste_datum=last,
                grafiek=yahoo_link(ticker),
            )
        )
    return result


def costs(amount, tax_pct=0):
    if (
        not math.isfinite(amount)
        or amount <= 0
        or not math.isfinite(tax_pct)
        or tax_pct < 0
    ):
        raise ValueError(
            "Bedrag moet positief/eindig en ingesteld kostenpercentage niet-negatief zijn"
        )
    cost = trade_cost(amount) + amount * tax_pct / 100
    return cost, cost / amount * 100


def report(ranking, title, lookback, top_n, capital, transaction, tax_pct=0, xgb=False):
    if min(top_n, capital, transaction) <= 0 or not all(
        math.isfinite(x) for x in (capital, transaction)
    ):
        raise ValueError("Ongeldig aantal picks of kapitaal")
    picks = ranking[:top_n]
    if not picks:
        return f"<b>{esc(title)} — {today_str()}</b>\nGeen kandidaten in het venster van {lookback} kalenderdagen."
    budget = capital / len(picks)
    amount = min(transaction, budget)
    if costs(amount, tax_pct)[0] + amount > budget:
        low, high = 0.0, amount
        for _ in range(60):
            mid = (low + high) / 2
            if mid + costs(mid, tax_pct)[0] <= budget:
                low = mid
            else:
                high = mid
        amount = math.floor(low * 100) / 100
    if amount <= 0:
        raise ValueError("Kapitaal te laag voor de kosten van dit aantal picks")
    cost, pct = costs(amount, tax_pct)
    lines = [
        f"<b>{esc(title)} — {today_str()}</b>",
        f"{len(picks)} picks | maximaal €{amount:,.2f} per pick binnen €{capital:,.2f} totaalbudget inclusief de vermelde kosten.",
        "Relatieve score 0–1 per strategiefamilie; geen rendementsverwachting.",
    ]
    for i, r in enumerate(picks, 1):
        lines += [
            f"\n{i}. <b>{esc(r['ticker'])}</b> ({esc(r['beurs'] or 'onbekende beurs')}) — signaaldatum {r['laatste_datum']}",
            f"Overlap: {r['overlap']} strategiefamilies — {esc(', '.join(r['strategieen']))}",
            f"Relatieve score: {r['avg_score']:.3f}",
        ]
        if r["koers"] is not None:
            lines.append(
                f"Bronkoers: {r['koers']:.8g} (bronvaluta/subeenheid; niet omgerekend naar EUR)."
            )
        else:
            lines.append("Geen geldige koers op de signaaldatum beschikbaar.")
        if xgb and r.get("heeft_xgb"):
            lines.append(
                f"XGBoost #{r['xgb_rang']}: {r['xgb_score']:.4f} | {esc(r['xgb_datum'])} | {esc(r['xgb_model'])}"
            )
        lines.append(f'<a href="{esc(yahoo_link(r["ticker"]))}">Grafiek</a>')
    lines += [
        f"Kostenindicatie per order: €{cost:.2f} ({pct:.2f}%). Max(vast, percentage), plus ingesteld extra percentage {tax_pct:g}%.",
        "Geen orders of positiegrootteberekening; valuta, spreads en niet ingestelde belastingen zijn niet inbegrepen.",
    ]
    return "\n".join(lines)


def check_delivery(telegram_ok, email_ok):
    errors = []
    if (
        os.getenv("TELEGRAM_TOKEN")
        and os.getenv("TELEGRAM_CHAT_ID")
        and not telegram_ok
    ):
        errors.append("Telegram niet verzonden")
    if (
        all(os.getenv(k) for k in ("EMAIL_USER", "EMAIL_PASS", "EMAIL_RECEIVER"))
        and not email_ok
    ):
        errors.append("E-mail niet verzonden")
    if errors:
        raise RuntimeError("; ".join(errors))
