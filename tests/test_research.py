"""Offline risk boundaries and reproducibility of the research adapter."""
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from research.signals import SelectionSignal
from research.allocation import AllocationPolicy, allocate


def signal(ticker="AAA", **kw):
    return SelectionSignal(ticker, "test", "2026-11-19", **{
        "reference_price": 100, "atr_pct": 4, "score": 1, **kw})


def test_cash_exposure_and_stop_risk_include_costs():
    policy = AllocationPolicy(max_gross_fraction=0.25)
    result = allocate([signal(str(i)) for i in range(20)], policy)
    assert result["remaining_cash"] >= 0
    assert result["gross_exposure"] <= policy.capital * 0.25
    for p in result["positions"]:
        assert p["notional"] <= policy.capital * 0.1
        assert p["estimated_stop_loss"] <= policy.capital * 0.01
    assert sum(p["notional"] + p["entry_commission"] for p in result["positions"]) + result["remaining_cash"] == pytest.approx(policy.capital)


def test_never_force_one_share_and_reject_impossible_stop():
    assert allocate([signal(reference_price=1e9)], AllocationPolicy())["positions"][0]["shares"] == 0
    assert allocate([signal(atr_pct=60)], AllocationPolicy())["positions"][0]["reason"] == "stop_not_positive"


@pytest.mark.parametrize("field", ["capital", "risk_fraction", "commission_min", "slippage_fraction"])
def test_nonfinite_policy(field):
    with pytest.raises(ValueError):
        AllocationPolicy(**{field: float("nan")})


def test_signal_validation_and_basket_boundaries():
    with pytest.raises(ValueError):
        signal(score=float("nan"))
    with pytest.raises(ValueError):
        allocate([signal(), signal()], AllocationPolicy())
    with pytest.raises(ValueError):
        allocate([signal(), replace(signal("B"), strategy="other")], AllocationPolicy())
    with pytest.raises(ValueError):
        allocate([signal(), replace(signal("B"), signal_date="2026-11-18")], AllocationPolicy())


def test_order_independence():
    signals = [signal("B"), signal("A")]
    assert allocate(signals, AllocationPolicy()) == allocate(signals[::-1], AllocationPolicy())


def test_cli_fingerprint_and_no_output_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "input.json"
    data = {"currency": "USD", "data_provenance": "synthetic test",
            "signals": [dict(ticker="A", strategy="test", signal_date="2026-11-19",
                             reference_price=100, atr_pct=4, score=1)]}
    path.write_text(json.dumps(data))
    def run():
        return json.loads(subprocess.check_output([sys.executable, "-m", "research", str(path)], cwd=root))
    first = run()
    assert run()["run_id"] == first["run_id"]
    data["signals"][0]["reference_price"] = 101
    path.write_text(json.dumps(data))
    assert run()["run_id"] != first["run_id"]
    assert list(tmp_path.iterdir()) == [path]

