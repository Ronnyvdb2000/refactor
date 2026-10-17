# Fantasie — Evaluatie en Audit

## Sterke punten
- 25+ strategieën, allemaal naar één `selecties`-tabel
- Twee modellen met walk-forward validatie (`a_par_model`, `a_meta_model`)
- Feature-stores (technicals + fundamentals + forward_returns)
- Uitstekende documentatie in docstrings
- Bewustzijn van eigen beperkingen (XGBoost AUC 0.51-0.53 = geen edge)

## Zwakke punten
1. **Codeduplicatie**: `download_history`, `_normalise`, `send_telegram`, etc. waren 15-25x gekopieerd.
2. **Inconsistente Wilder-implementaties**: loop vs ewm met/zonder min_periods. Dit is nu opgelost in `common/indicators.py`.
3. **Geen unit tests**: nu opgelost met `tests/`.
4. **Yahoo Finance = single point of failure**: nog niet opgelost (Fase 4).
5. **Feature-store wordt niet overal gebruikt**: bots berekenen nog eigen indicatoren.
6. **Forward-return overlap**: te optimistische p-waarden in IC-analyses (Fase 3).
7. **FDR per horizon**: zou over alle horizonnen samen moeten (Fase 3).

## Methodologische kanttekeningen
- Newey-West of block-bootstrap nodig voor IC-t-toetsen (Fase 3).
- Backtests zijn kort (1-5 jaar), gebruiken soms technische proxy's.
- Geen rekening met werkelijke liquiditeit bij slippage.

## Voortgang
- [x] **Fase 1: Fundament** — `common/` package, `tests/`, `common.py` shim.
- [ ] **Fase 2: Bots migreren** — per bot vervangen door `common/`.
- [ ] **Fase 3: Statistiek** — Newey-West, FDR over alle horizonnen.
- [ ] **Fase 4: Databron** — fallback voor Yahoo.
- [ ] **Fase 5: CI** — GitHub Actions voor pytest.
