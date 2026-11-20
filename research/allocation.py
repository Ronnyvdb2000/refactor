"""Cost-aware long-only basket simulation in one explicitly supplied currency.

Each call starts with cash only. It does not model an existing portfolio or fills.
"""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class AllocationPolicy:
    capital: float = 50000.0
    risk_fraction: float = 0.01
    max_position_fraction: float = 0.10
    max_gross_fraction: float = 0.80
    atr_stop_multiple: float = 2.0
    commission_min: float = 15.0
    commission_fraction: float = 0.0035
    slippage_fraction: float = 0.001

    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Niet-eindige configuratie")
        if self.capital <= 0 or self.atr_stop_multiple <= 0:
            raise ValueError("Kapitaal en ATR-multiple moeten positief zijn")
        if not all(0 < v <= 1 for v in (self.risk_fraction,
                   self.max_position_fraction, self.max_gross_fraction)):
            raise ValueError("Risicolimieten moeten in (0, 1] liggen")
        if self.commission_min < 0 or self.commission_fraction < 0:
            raise ValueError("Negatieve commissie")
        if not 0 <= self.slippage_fraction < 1:
            raise ValueError("Slippage moet in [0, 1) liggen")

    def commission(self, value):
        return max(self.commission_min, value * self.commission_fraction) if value else 0.0


def allocate(signals, policy):
    """Allocate by descending raw score within one strategy/date; reject duplicates.

    Risk includes adverse entry slippage and estimated entry/stop-exit commission.
    Stops are assumptions, not guaranteed maximum losses in a gap.
    """
    signals = list(signals)
    if len({s.ticker for s in signals}) != len(signals):
        raise ValueError("Dubbele ticker in mandje")
    if len({(s.strategy, s.signal_date) for s in signals}) > 1:
        raise ValueError("Gebruik één strategie en één signaaldatum per mandje")
    cash, exposure = policy.capital, 0.0
    result = []
    for s in sorted(signals, key=lambda s: (-s.score, s.ticker)):
        stop = s.reference_price * (1 - policy.atr_stop_multiple * s.atr_pct / 100)
        entry = s.reference_price * (1 + policy.slippage_fraction)
        exit_price = stop * (1 - policy.slippage_fraction)
        shares = 0
        reason = "stop_not_positive"
        if stop > 0:
            cap = min(policy.capital * policy.max_position_fraction,
                      max(0, policy.capital * policy.max_gross_fraction - exposure), cash)
            # Monotonic constraints allow bounded binary search, even for penny stocks.
            lo, hi = 0, int(cap / entry)
            while lo < hi:
                n = (lo + hi + 1) // 2
                value = n * entry
                fee = policy.commission(value)
                loss = n * (entry - exit_price) + fee + policy.commission(n * exit_price)
                if value + fee <= cash and loss <= policy.capital * policy.risk_fraction:
                    lo = n
                else:
                    hi = n - 1
            shares = lo
            reason = "allocated" if shares else "budget_or_risk_below_one_share"
        value = shares * entry
        fee = policy.commission(value)
        risk = shares * (entry - exit_price) + fee + policy.commission(shares * exit_price) if shares else 0.0
        cash -= value + fee
        exposure += value
        result.append({"ticker": s.ticker, "shares": shares, "entry": entry,
                       "stop": stop, "notional": value, "entry_commission": fee,
                       "estimated_stop_loss": risk, "reason": reason})
    return {"positions": result, "remaining_cash": cash, "gross_exposure": exposure,
            "estimated_stop_loss": sum(r["estimated_stop_loss"] for r in result)}
