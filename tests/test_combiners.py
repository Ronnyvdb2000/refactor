"""Offline combiner regressions; no live scans, notifications or database writes."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
import yaml
from combiners import (
    a_trade as best,
    a_trade_q as fresh,
    a_trade_combi as weighted,
    shared,
)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    for module in [shared, best, fresh, weighted]:
        monkeypatch.setattr(module, "today_str", lambda: "2026-11-14")
    for key in [
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
        "EMAIL_USER",
        "EMAIL_PASS",
        "EMAIL_RECEIVER",
    ]:
        monkeypatch.delenv(key, raising=False)


def row(ticker="A", strategy="bot_00vcp", day="2026-11-13", score=5, price=20, **extra):
    return dict(
        ticker=ticker,
        strategie=strategy,
        datum=day,
        score=score,
        koers=price,
        beurs="market",
        **extra,
    )


def test_latest_quote_does_not_depend_on_input_order():
    values = [row(day="2026-11-12", price=10), row(price=20, strategy="bot_00kr")]
    for rows in [values, values[::-1]]:
        r = best.bouw_ranking(rows)[0]
        assert r["koers"] == 20 and r["laatste_datum"] == "2026-11-13"


def test_old_price_is_not_presented_as_latest_date():
    r = best.bouw_ranking(
        [row(day="2026-11-12", price=10), row(price=None, strategy="bot_00kr")]
    )[0]
    assert r["koers"] is None


def test_duplicates_do_not_change_overlap_or_relative_score():
    values = [row(), row(strategy="bot_00kr"), row("B", score=3)]
    assert best.bouw_ranking(values) == best.bouw_ranking(values + [values[0]] * 10)


def test_live_full_and_old_names_count_as_same_strategy_family():
    values = [row(strategy="bot_01hoogl"), row(strategy="bot_01hoogl_full")]
    assert best.bouw_ranking(values)[0]["overlap"] == 1
    assert fresh.bouw_ranking(values) == []


def test_derived_output_cannot_confirm_itself_or_its_inputs():
    values = [
        row(),
        row(strategy="vers_signaal_1d"),
        row(strategy="bot_combi_volatiel"),
        row(strategy="a_par_model_v1"),
    ]
    assert best.bouw_ranking(values)[0]["overlap"] == 1
    assert fresh.bouw_ranking(values) == []


def test_raw_score_scale_does_not_determine_cross_strategy_average():
    values = [
        row(score=10),
        row("B", score=5),
        row(strategy="bot_00kr", score=0.1),
        row("B", strategy="bot_00kr", score=0.9),
    ]
    original = best.bouw_ranking(values)
    changed = [
        dict(r, score=r["score"] * 1000 + 500) if r["strategie"] == "bot_00kr" else r
        for r in values
    ]
    assert best.bouw_ranking(changed) == original
    assert {r["avg_score"] for r in original} == {0.5}


def test_fresh_requires_two_families_on_same_latest_date_per_ticker():
    values = [
        row(day="2026-11-12"),
        row(strategy="bot_00kr", day="2026-11-12"),
        row(),
        row("B", day="2026-11-12"),
        row("B", strategy="bot_00kr", day="2026-11-12"),
    ]
    r = fresh.bouw_ranking(values)
    assert [x["ticker"] for x in r] == ["B"]
    assert r[0]["laatste_datum"] == "2026-11-12"


def test_invalid_and_future_rows_are_excluded_and_nan_score_neutral():
    values = [
        row(score=float("nan")),
        row("B", day="2026-11-15"),
        row("C", day="2026-99-99"),
        row("D", day="2026-11-01"),
    ]
    result = best.bouw_ranking(values)
    assert len(result) == 1 and result[0]["avg_score"] == 0.5


def test_xgb_requires_matching_signal_date():
    values = [row()]
    x = {"A": dict(score=0.9, beste_rang=1, datum="2026-11-12", model_versie="v1")}
    assert not best.bouw_ranking(values, x)[0]["heeft_xgb"]
    x["A"]["datum"] = "2026-11-13"
    assert best.bouw_ranking(values, x)[0]["heeft_xgb"]


def test_xgb_query_uses_one_snapshot_not_historical_extremes(monkeypatch):
    monkeypatch.setattr(best, "SUPABASE_DB_URL", "offline")
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = []
    with patch.object(best.psycopg2, "connect", return_value=conn):
        best.haal_xgboost_scores_op(3)
    query = cur.execute.call_args.args[0]
    assert "MAX(score)" not in query and "MIN(rang)" not in query
    assert "JOIN snapshot" in query and "row_number()" in query


def test_costs_use_shared_minimum_and_explicit_extra_percentage():
    assert shared.costs(2500) == (15, 0.6)
    assert shared.costs(10000, 1) == (135, 1.35)
    with pytest.raises(ValueError):
        shared.costs(0)


def test_report_budget_includes_costs_and_escapes_source_text(monkeypatch):
    ranking = best.bouw_ranking([row(ticker=t) for t in ["<A>", "B", "C", "D", "E"]])
    text = best.maak_telegram_bericht(ranking, 3)
    assert "€485.00 per pick" in text
    assert "&lt;A&gt;" in text and "<b><A></b>" not in text
    assert "bronvaluta" in text and "EUR" in text


def test_fresh_storage_uses_signal_date_and_checks_confirmation():
    ranking = fresh.bouw_ranking([row(), row(strategy="bot_00kr")])
    with patch.object(fresh, "log_selectie", return_value=True) as log:
        assert fresh.log_picks(ranking) == 1
        assert log.call_args.kwargs["datum"] == "2026-11-13"
    with (
        patch.object(fresh, "log_selectie", return_value=False),
        pytest.raises(RuntimeError),
    ):
        fresh.log_picks(ranking)


def test_missing_fresh_price_is_not_stored():
    ranking = fresh.bouw_ranking(
        [row(price=None), row(strategy="bot_00kr", price=None)]
    )
    with patch.object(fresh, "log_selectie") as log, pytest.raises(RuntimeError):
        fresh.log_picks(ranking)
    log.assert_not_called()


def test_notification_failure_is_visible_when_channel_configured(monkeypatch):
    monkeypatch.setenv("EMAIL_USER", "offline")
    monkeypatch.setenv("EMAIL_PASS", "offline")
    monkeypatch.setenv("EMAIL_RECEIVER", "offline")
    with pytest.raises(RuntimeError, match="E-mail"):
        shared.check_delivery(False, False)


def history(ticker="A", n=400):
    return pd.DataFrame(
        dict(
            Ticker=ticker,
            Date=pd.bdate_range(end="2026-11-13", periods=n),
            Open=20.0,
            High=22.0,
            Low=18.0,
            Close=20.0,
            Volume=100.0,
        )
    )


def test_weighted_reuses_technical_download_and_has_no_dividend_fetch():
    raw = history()
    quotes = {}
    with patch.object(weighted, "download_universe") as download:
        result = weighted.compute_atr_pct({"market": ["A"]}, ["A"], quotes, data=raw)
    download.assert_not_called()
    assert result["A"] == 20 and quotes["A"] == (20.0, "2026-11-13")


def test_bad_latest_bar_does_not_become_older_signal():
    raw = history()
    raw.loc[len(raw) - 1, "Close"] = float("nan")
    with pytest.raises(ValueError, match="laatste OHLCV"):
        weighted.prepare_daily(raw)


def test_missing_seasonal_history_does_not_abort_other_tickers():
    raw = pd.concat([history("A"), history("B")])
    candidate = SimpleNamespace(ticker="B")
    with (
        patch.object(weighted, "download_universe", return_value=raw),
        patch.object(
            weighted.repititief,
            "analyseer_ticker",
            side_effect=[weighted.repititief.DataUnavailable("short"), candidate],
        ),
        patch.object(
            weighted.repititief, "pas_bh_toe_op_beurs", return_value=[candidate]
        ),
    ):
        assert weighted.selecties_repititief({"market": ["A", "B"]}, ["A", "B"]) == {
            "market": {"B"}
        }


@pytest.mark.parametrize(
    "name,module", [("kasstr", "kasstr"), ("fisher", "fisher"), ("hoogl", "hoogl")]
)
def test_fundamentals_cache_across_exchange_lists(name, module):
    target = getattr(weighted, module)
    value = SimpleNamespace(score=100)
    if name == "hoogl":
        value = (value, False)
    with (
        patch.object(target, "analyse_ticker", return_value=value) as analyse,
        patch.object(weighted.time, "sleep"),
    ):
        result = getattr(weighted, "selecties_" + name)({"one": {"A"}, "two": {"A"}})
        analyse.assert_called_once()
        assert result == {"one": {"A"}, "two": {"A"}}


def test_rate_limits_do_not_turn_into_successful_empty_scan():
    with (
        patch.object(weighted, "RETRY_POGINGEN", 2),
        patch.object(weighted.time, "sleep"),
    ):
        with pytest.raises(RuntimeError):
            weighted.met_retry(MagicMock(side_effect=RuntimeError("429")), "A", "test")


def test_weight_minimum_counts_dates_and_negative_history_gets_zero(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "offline")
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [
        dict(strategie="bot_00vcp", n=40, gem=-2, std=1),
        dict(strategie="bot_01kasstr", n=40, gem=2, std=1),
        dict(strategie="bot_00Fisher", n=2, gem=10, std=1),
    ]
    with patch.object(weighted.psycopg2, "connect", return_value=conn):
        weights = weighted.herbereken_gewichten()
    assert weights["vcp"] == 0 and weights["kasstr"] > 0
    assert weights["fisher"] == weighted.GEWICHTEN_FALLBACK["fisher"]
    query, params = cur.execute.call_args.args
    assert "GROUP BY strategie,datum" in query and params["cutoff"] < "2026-11-14"


def test_weight_horizon_is_validated_before_sql(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "offline")
    monkeypatch.setattr(weighted, "GEWICHT_HORIZON", "bad;drop")
    with pytest.raises(ValueError):
        weighted.herbereken_gewichten()


def test_all_modules_and_workflow_commands_resolve_without_live_run():
    for name in ["a_trade", "a_trade_q", "a_trade_combi"]:
        result = subprocess.run(
            [sys.executable, "-m", "combiners." + name, "--help"],
            text=True,
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        content = Path(".github/workflows/" + name + ".yml").read_text()
        jobs = yaml.safe_load(content)["jobs"]
        runs = [step.get("run", "") for job in jobs.values() for step in job["steps"]]
        assert "python -m combiners." + name in runs
        assert not Path(name + ".py").exists()


def test_window_contains_exactly_configured_calendar_dates():
    result = best.bouw_ranking([row("A", day="2026-11-11"), row("B", day="2026-11-12")])
    assert [r["ticker"] for r in result] == ["B"]


def test_xgb_snapshot_sql_in_standalone_postgresql(tmp_path, monkeypatch):
    from psycopg2 import extensions

    pgserver = pytest.importorskip("pgserver")
    monkeypatch.setattr(best, "SUPABASE_DB_URL", "offline")
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = []
    with patch.object(best.psycopg2, "connect", return_value=conn):
        best.haal_xgboost_scores_op(3)
    query, params = cur.execute.call_args.args
    query = query % tuple(extensions.adapt(v).getquoted().decode() for v in params)
    pgserver.initdb(["-A", "trust", "--no-locale"], pgdata=tmp_path)
    setup = """CREATE TABLE xgboost_scores(ticker text,datum text,model_versie text,score float8,gescoord_op timestamptz,horizon text);
    INSERT INTO xgboost_scores VALUES ('A','2026-11-12','v1',99,'2026-11-12','10d'),('A','2026-11-13','v2',.3,'2026-11-14','10d'),('B','2026-11-13','v2',.8,'2026-11-14','10d'),('A','2026-11-13','older_model',999,'2026-11-13','10d'),('A','2026-11-15','v2',9999,'2026-11-15','10d');"""
    script = (
        " ".join(setup.splitlines())
        + "\nCREATE TABLE selected AS "
        + " ".join(query.splitlines())
        + ";\n"
    )
    script += "DO $$ BEGIN IF (SELECT count(*) FROM selected)<>2 OR NOT EXISTS(SELECT 1 FROM selected WHERE ticker='B' AND beste_rang=1 AND model_versie='v2') OR NOT EXISTS(SELECT 1 FROM selected WHERE ticker='A' AND score=.3 AND beste_rang=2) THEN RAISE EXCEPTION 'snapshot incoherent'; END IF; END $$;\n"
    binary = pgserver.postgres.__globals__["POSTGRES_BIN_PATH"] / "postgres"
    result = subprocess.run(
        [str(binary), "--single", "-D", str(tmp_path), "postgres"],
        input=script,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0 and "ERROR:" not in result.stderr, result.stderr


@pytest.mark.parametrize("swallowed", [False, True])
def test_weighted_detects_logged_auth_failure_even_when_strategy_swallows_it(swallowed):
    import logging

    def broken():
        logging.getLogger("yfinance").error('HTTP Error 401: Invalid Crumb')
        if not swallowed:
            raise weighted.kasstr.DataUnavailable("ongeldige koers")
        return None

    logger = logging.getLogger("yfinance")
    original = list(logger.handlers)
    with patch.object(weighted.time, "sleep") as sleep:
        with pytest.raises(RuntimeError, match="Yahoo weigert"):
            weighted.met_retry(broken, "A", "kasstr")
        sleep.assert_not_called()
    assert logger.handlers == original


def test_weighted_detects_logged_rate_limit_swallowed_by_fisher():
    import logging

    def broken():
        logging.getLogger("yfinance").error("HTTP Error 429: Too Many Requests")
        return None

    with (
        patch.object(weighted, "RETRY_POGINGEN", 2),
        patch.object(weighted.time, "sleep") as sleep,
        pytest.raises(RuntimeError, match="Yahoo beperkt"),
    ):
        weighted.met_retry(broken, "A", "fisher")
    sleep.assert_called_once()


def test_weighted_does_not_confuse_individual_missing_data_with_provider_failure():
    with patch.object(weighted.time, "sleep") as sleep:
        assert weighted.met_retry(
            MagicMock(side_effect=weighted.kasstr.DataUnavailable("geen jaarlijkse cashflow")),
            "A", "kasstr"
        ) is None
    sleep.assert_not_called()


@pytest.mark.parametrize("name", ["kasstr", "fisher", "hoogl"])
def test_weighted_all_missing_fundamentals_is_incomplete(name):
    module = getattr(weighted, name)
    kwargs = {"return_value": (None, False) if name == "hoogl" else None}
    if name == "kasstr":
        kwargs = {"side_effect": weighted.kasstr.DataUnavailable("ongeldige koers")}
    with (
        patch.object(module, "analyse_ticker", **kwargs),
        patch.object(weighted.time, "sleep"),
        pytest.raises(RuntimeError, match="geen bruikbare fundamentele analyses"),
    ):
        getattr(weighted, "selecties_" + name)({"market": {"A", "B"}})


@pytest.fixture
def weighted_scan(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "offline")
    monkeypatch.setenv("TELEGRAM_TOKEN", "offline")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "offline")
    monkeypatch.setattr(weighted, "herbereken_gewichten", lambda: {"kasstr": 1, "fisher": 0, "hoogl": 0, "vcp": 0, "repititief": 0})
    monkeypatch.setattr(weighted, "bouw_exchange_tickers", lambda: ({"market": ["A"]}, ["A"]))
    monkeypatch.setattr(weighted, "warm_up_yfinance", lambda: True)
    monkeypatch.setattr(weighted, "download_universe", lambda *args: history())
    monkeypatch.setattr(weighted, "selecties_repititief", lambda *args: {"market": set()})
    monkeypatch.setattr(weighted, "selecties_vcp", lambda *args, **kwargs: {"market": {"A"}})
    monkeypatch.setattr(weighted.time, "sleep", lambda *args: None)
    with (
        patch.object(weighted, "send_telegram", return_value=True) as telegram,
        patch.object(weighted, "send_email", return_value=True) as email,
        patch.object(weighted.db_logger, "log_selectie", return_value=True) as store,
        patch.object(weighted, "selecties_fisher") as fisher,
        patch.object(weighted, "selecties_hoogl") as hoogl,
    ):
        yield SimpleNamespace(telegram=telegram, email=email, store=store, fisher=fisher, hoogl=hoogl)


def test_weighted_auth_failure_notifies_and_stops_before_publication(weighted_scan):
    import logging

    def broken(*args, **kwargs):
        logging.getLogger("yfinance").error("HTTP Error 401: Invalid Crumb")
        raise weighted.kasstr.DataUnavailable("ongeldige koers")

    with patch.object(weighted.kasstr, "analyse_ticker", side_effect=broken) as analyse:
        with pytest.raises(RuntimeError, match="Yahoo weigert"):
            weighted.run_live_engine()
    analyse.assert_called_once()
    weighted_scan.store.assert_not_called()
    weighted_scan.fisher.assert_not_called()
    weighted_scan.hoogl.assert_not_called()
    weighted_scan.telegram.assert_called_once()
    assert "ONVOLLEDIG" in weighted_scan.telegram.call_args.args[0]
    assert weighted_scan.telegram.call_args.kwargs["parse_mode"] is None
    assert "ONVOLLEDIG" in weighted_scan.email.call_args.kwargs["subject"]


def test_weighted_valid_empty_report_sends_telegram_and_skips_zero_weights(weighted_scan):
    with patch.object(weighted.kasstr, "analyse_ticker", return_value=SimpleNamespace(score=-1)):
        weighted.run_live_engine()
    weighted_scan.store.assert_not_called()
    weighted_scan.fisher.assert_not_called()
    weighted_scan.hoogl.assert_not_called()
    weighted_scan.telegram.assert_called_once()
    assert "Geen kandidaten" in weighted_scan.telegram.call_args.args[0]


def test_weighted_empty_report_delivery_failure_does_not_succeed(weighted_scan):
    weighted_scan.telegram.return_value = False
    with patch.object(weighted.kasstr, "analyse_ticker", return_value=SimpleNamespace(score=-1)):
        with pytest.raises(RuntimeError, match="Telegram leeg rapport"):
            weighted.run_live_engine()
    assert "ONVOLLEDIG" in weighted_scan.telegram.call_args.args[0]


def test_weighted_notification_exception_preserves_original_scan_failure():
    with (
        patch.object(weighted, "_run_live_engine", side_effect=RuntimeError("scan failed")),
        patch.object(weighted, "send_telegram", side_effect=RuntimeError("delivery failed")),
        patch.object(weighted, "send_email", return_value=True) as email,
        pytest.raises(RuntimeError, match="scan failed"),
    ):
        weighted.run_live_engine()
    email.assert_called_once()


def test_weighted_successful_selection_retains_telegram_and_signal_date(weighted_scan):
    with patch.object(weighted.kasstr, "analyse_ticker", return_value=SimpleNamespace(score=100)):
        weighted.run_live_engine()
    weighted_scan.store.assert_called_once()
    assert weighted_scan.store.call_args.kwargs["datum"] == "2026-11-13"
    weighted_scan.telegram.assert_called_once()
    assert "`A`" in weighted_scan.telegram.call_args.args[0]
    assert "ONVOLLEDIG" not in weighted_scan.telegram.call_args.args[0]
