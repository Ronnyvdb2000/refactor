# Onderzoekslaag: principes uit Quantify

Branch: `feature/quantify-principles`.

## Bron en aanpak

Gelezen op 2026-11-19: [Quantify README](https://github.com/tushhh/quantify/blob/main/README.md),
[signaalmodel](https://github.com/tushhh/quantify/blob/main/src/quantify/strategy/signal.py),
[risicolimieten](https://github.com/tushhh/quantify/blob/main/src/quantify/risk/limits.py),
[kostenmodel](https://github.com/tushhh/quantify/blob/main/src/quantify/backtest/costs.py)
en [licentie](https://github.com/tushhh/quantify/blob/main/LICENSE).
De licentie geeft geen toestemming voor codeovername. Deze laag is een eigen implementatie van algemene ontwerpprincipes; er zijn geen bronbestanden of strategieën gekopieerd.

| Principe | Implementatie in refactor |
| --- | --- |
| Scheid selectie en allocatie | `research/signals.py` versus `research/allocation.py` |
| Centrale configuratie | `config/research.json`, strikt gevalideerd |
| Risico vóór positiegrootte | Hele aandelen, ATR-stop, positie- en totale blootstellingslimiet |
| Realistische kosten | Minimum/procentcommissie per transactie plus ongunstige slippage bij instap en stop-uitstap |
| Reproduceerbaarheid | JSON-manifest met signalen, databron, configuratie, git-revisie en codehashes |
| Gefaseerde integratie | `combiners.a_trade_combi` bewaart extra `parameters.research_signal`; bestaande velden en Telegram blijven behouden |

De nieuwe kostenaannames sluiten aan bij de huidige gedeelde constanten, maar zijn afzonderlijk configureerbaar. Bestaande sizing- en backtestfuncties zijn niet gewijzigd.

## Offline gebruiken

Maak een JSON-invoer met koersen in **één gemeenschappelijke valuta en prijseenheid**:

```json
{
  "currency": "USD",
  "data_provenance": "synthetisch voorbeeld; geen marktdata",
  "signals": [
    {"ticker": "DEMO", "strategy": "voorbeeld", "signal_date": "2026-11-19",
     "reference_price": 100, "atr_pct": 4, "score": 1}
  ]
}
```

```bash
python -m research invoer.json --config config/research.json
python -m pytest tests/test_research.py -q
```

Uitvoer is uitsluitend JSON op stdout. Geen automatische bestanden, downloads, databasewijzigingen of notificaties. De combinerpayload bevat ook `schema_version` en `direction`; neem bij CLI-invoer de velden uit bovenstaand voorbeeld over. De databronbeschrijving moet ook eventuele FX-conversie en koersaanpassingen vastleggen.

Een run start met een leeg, fictief cashmandje. Rangschikking gebeurt op ruwe score binnen één strategie en datum; gemengde strategieën/datums en dubbele tickers worden geweigerd. Gelijke scores worden op ticker gesorteerd. Koersen worden niet opgehaald of naar een andere valuta geconverteerd. De caller is verantwoordelijk voor een uniforme valuta; de combiner levert daarom nog geen automatische portefeuille-allocatie over verschillende beurzen.

De standaardinstellingen (50.000 kapitaal, 1% geschat stoprisico, 10% per positie, 80% totale blootstelling) zijn onderzoeksdefaults. Stops garanderen geen verliesgrens bij koersgaten. Het totaal geschatte stopverlies staat expliciet in de uitvoer. Een positieve koers met te weinig budget levert nul aandelen op.

## Grenzen en verdere migratie

Dit is een selectieschema en allocatiesimulator, geen event-driven backtester en geen broker/paper-tradingadministratie. Er zijn nog geen historische fills, bestaande posities, sectorlimieten, valutaomrekening, benchmarkrendementen of walk-forward-evaluaties toegevoegd. Voeg die pas toe met expliciete data over sectoren, FX, tijdstip en portefeuilletoestand; een actuele tickerlijst is geen historisch universum.

De koppeling is nu geïmplementeerd voor de volatiele combiner; overige bots gebruiken nog hun bestaande formaat. Training/scoring, TradingAgents en `refactor-lab` zijn niet gewijzigd. De CI-workflow test offline en heeft geen secrets nodig. De branch wordt niet automatisch met `main` samengevoegd.

## Telegram

`python -m research invoer.json --telegram` stuurt het berekende mandje naar de bestaande `TELEGRAM_TOKEN` en `TELEGRAM_CHAT_ID`. Zonder die vlag blijft de CLI offline. Ontbrekende secrets of mislukte verzending laten de opdracht falen; een eerdere deelverzending kan al ontvangen zijn.

De workflow **Research principles checks** stuurt voortaan de teststatus met een link naar de run. Dit is expliciet een testbericht, geen beursresultaat. Bij handmatig starten kun je optioneel `signals_json` invullen met het JSON-mandje uit het voorbeeld hierboven; na geslaagde tests gaat ook de simulatie naar Telegram. Er wordt nog geen mandje automatisch uit Supabase geladen. Geen CSV-export, geen orders. PR-runs versturen geen teststatus naar Telegram.


## Automatisch mandje uit Supabase

Workflow **Research USD simulation to Telegram** op `feature/quantify-principles` leest uitsluitend uit `selecties` en stuurt de berekening via de bestaande Telegram-secrets. Start met Run workflow; wijzigingen aan de database-adapter of deze workflow op de onderzoeksbranch starten ook een run. CLI: `python -m research --supabase --telegram`.

De eerste adapter ondersteunt alleen `bot_combi_volatiel` / `048 Nasdaq/NYSE`. Het USD-label volgt de marktconventie van deze lijst; er is geen FX-conversie of onafhankelijke valutaverificatie per ticker. Een expliciet afwijkende valuta in parameters wordt geweigerd. Andere beurzen worden niet bijgemengd. Zorg dat deze lijst uitsluitend USD-genoteerde symbolen bevat.

Er wordt één recentste koersdatum gebruikt (maximaal zeven kalenderdagen oud), met één rij per ticker en de hoogste rij-ID bij duplicaten. Oude rijen worden niet gebruikt om ontbrekende nieuwe gegevens op te vullen. De adapter accepteert ook bestaande combinerparameters (ATR, gewogen score, prijsbasis en signaaldatum); de nieuwe research-payload is niet verplicht. De verbinding is read-only met connect- en querytimeout. Ontbrekende of ongeldige data laat de run falen en veroorzaakt een ONVOLLEDIG-melding. De bronkoersdatum verschijnt in het Telegram-rapport. Dit is geen koersactualisatie en geen bewijs dat een volledige bronscan slaagde.

Geen databasewrites, downloads van actuele koersen, CSV-bestanden of orders. Het fictieve kapitaal wordt voor elke simulatie opnieuw gebruikt; er wordt geen echte portefeuille gevolgd.
