"""Unit tests voor sizing en kosten."""
import pytest
from common import (
    trade_cost, bereken_positie, bereken_atr_positie, sizing_tekst,
    TRADE_COST_FIXED, TRADE_COST_PCT,
)

def test_trade_cost_formule():
    assert trade_cost(1000) == pytest.approx(TRADE_COST_FIXED + 1000 * TRADE_COST_PCT)

def test_positie_risico_5_procent():
    aandelen, max_verlies = bereken_positie(
        portfolio_waarde=100_000, entry_prijs=100, stop_prijs=95, risico_pct=0.05)
    assert aandelen == 1000
    assert max_verlies == pytest.approx(5000.0)

def test_positie_met_negatieve_stopafstand_geeft_nul():
    aandelen, _ = bereken_positie(100_000, 100, 105)
    assert aandelen == 0

def test_atr_positie():
    aandelen, stop, max_verlies = bereken_atr_positie(
        portfolio_waarde=100_000, entry_prijs=50, atr=1.0,
        sl_mult=2.0, risico_pct=0.05)
    assert aandelen == 2500
    assert stop == pytest.approx(48.0)
    assert max_verlies == pytest.approx(5000.0)

def test_sizing_tekst_bevat_verwachte_velden():
    tekst = sizing_tekst(entry=100, stop=95, tp=110, portfolio_waarde=50_000)
    assert "Entry" in tekst and "Stop" in tekst and "TP" in tekst
    assert "R/R" in tekst
