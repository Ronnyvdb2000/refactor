# Fantasie — Evaluatie en Audit

## Repo-overzicht en architectuur

De repo is geen standaard applicatie, maar een quant/research pipeline voor aandelenselectie en modelbouw. De kern van het project is als volgt:

- De tickers van verschillende beurzen staan in bestanden als `tickers_0??a.txt`, waarbij `??` varieert van `01` tot `099`.
- Deze tickers worden wekelijks geselecteerd op kwaliteit volgens `bot_041m.py`.
- De selectie wordt aangevuld of verwijderd en opgeslagen in bestanden zoals `tickers_0??m.txt`.
- De geselecteerde aandelen worden in een Supabase-database weggeschreven, samen met de parameters waarop ze geselecteerd zijn.
- Naast deze selectieparameters worden ook alle andere relevante parameters berekend.
- Op basis van deze selecties worden verschillende meta-modellen opgebouwd die aandelen selecteren met een verwachte koersstijging voor verschillende horizonten:
  - 5 dagen
  - 10 dagen
  - 20 dagen
  - 30 dagen
  - 60 dagen

Deze structuur wijst op een dataflow van:

1. Ticker-bronnen verzamelen
2. Selectie op kwaliteit
3. Opslaan in database
4. Feature-engineering / parameterberekening
5. Training / validatie van meta-modellen
6. Scoring en selectie van kansrijke aandelen per horizon

## Sterke punten
- 25+ strategieën, allemaal naar één `selecties`-tabel
- Twee modellen met walk-forward validatie (`a_par_model`, `a_meta_model`)
- Feature-stores (technicals + fundamentals + forward_returns)
- Uitstekende documentatie in docstrings
- Bewustzijn van eigen beperkingen (XGBoost AUC 0.51-0.53 = geen edge)
- Duidelijke data- en modelstroom voor selectie van tickers en modellen
- Relevante scheiding tussen bron-data, selectie, database en modeleren

## Zwakke punten
1. **Codeduplicatie**: `download_history`, `_normalise`, `send_telegram`, etc. waren 15-25x gekopieerd.
2. **Inconsistente Wilder-implementaties**: loop vs ewm met/zonder min_periods. Dit is nu opgelost in `common/indicators.py`.
3. **Geen unit tests**: nu opgelost met `tests/`.
4. **Yahoo Finance = single point of failure**: nog niet opgelost (Fase 4).
5. **Feature-store wordt niet overal gebruikt**: bots berekenen nog eigen indicatoren.
6. **Forward-return overlap**: te optimistische p-waarden in IC-analyses (Fase 3).
7. **FDR per horizon**: zou over alle horizonten samen moeten (Fase 3).
8. **Architectuur is nog deels script-gebaseerd**: veel logica ligt verspreid over losse Python-bestanden in plaats van in een centrale service- of package-architectuur.

## Methodologische kanttekeningen
- Newey-West of block-bootstrap nodig voor IC-t-toetsen (Fase 3).
- Backtests zijn kort (1-5 jaar), gebruiken soms technische proxy's.
- Geen rekening met werkelijke liquiditeit bij slippage.
- Modelresultaten zijn mogelijk gevoelig voor selectie bias en data snooping, vooral bij meerdere horizonten en meerdere kandidaatmodellen.
- Het gebruik van meerdere verschillende modelvarianten kan de interpretatie van prestaties bemoeilijken als er geen duidelijke validatieroute is.

## Voortgang
- [x] **Fase 1: Fundament** — `common/` package, `tests/`, `common.py` shim.
- [ ] **Fase 2: Bots migreren** — per bot vervangen door `common/`.
- [ ] **Fase 3: Statistiek** — Newey-West, FDR over alle horizonten.
- [ ] **Fase 4: Databron** — fallback voor Yahoo.
- [ ] **Fase 5: CI** — GitHub Actions voor pytest.

## Conclusie

De repo is een goed voorbeeld van een quant research pipeline die in opbouw is naar een meer structureel en reproduceerbaar systeem. De kernidee is sterk: een wekelijkse tickerselectie op kwaliteit, gestuwd door feature-engineering en meta-modellen voor verschillende rendementshorizonten. De architectuur is functioneel begrijpelijk en logisch, maar nog niet volledig gestandaardiseerd of gedecentraliseerd.

De belangrijkste verbetering zit in de refactor- en consolidatiewerkzaamheden: het reduceren van duplicatie, centraliseren van indicatorlogica, standaardiseren van de feature-store en verbeteren van de statistische validatie. Als die stappen worden voltooid, heeft de repo een veel grotere kans om robust, reproduceerbaar en production-vriendelijk te worden.
