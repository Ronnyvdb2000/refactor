"""Versioned description of a selection, independent of position sizing."""
from dataclasses import asdict, dataclass
from datetime import date
import math


@dataclass(frozen=True)
class SelectionSignal:
    ticker: str
    strategy: str
    signal_date: str
    reference_price: float
    atr_pct: float
    score: float
    price_basis: str = "yahoo_auto_adjusted_close"

    def __post_init__(self):
        if not self.ticker.strip() or not self.strategy.strip():
            raise ValueError("Ticker en strategie zijn verplicht")
        date.fromisoformat(self.signal_date)
        if not all(math.isfinite(v) for v in
                   (self.reference_price, self.atr_pct, self.score)):
            raise ValueError("Signaal bevat niet-eindige waarden")
        if self.reference_price <= 0 or self.atr_pct <= 0 or self.score < 0:
            raise ValueError("Ongeldige koers, ATR of score")

    def payload(self):
        return {"schema_version": 1, "direction": "long", **asdict(self)}
