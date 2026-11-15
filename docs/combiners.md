# Combineerders — structuur en controle

De combineerders van `Ronnyvdb2000/refactor` staan in `combiners/`. Start ze vanuit de repository-root. De basisbots, tickerlijsten, databasehelpers en modellen staan voorlopig op hun bestaande plaats.

| Module | Functie | Workflow |
| --- | --- | --- |
| [combiners.a_trade](../combiners/a_trade.py) | Overlap van strategiefamilies binnen het ingestelde kalenderdagvenster; verrijkt met een coherente XGBoost-snapshot | [a_trade.yml](../.github/workflows/a_trade.yml) |
| [combiners.a_trade_q](../combiners/a_trade_q.py) | Minstens twee strategiefamilies op de laatste beschikbare signaaldatum per ticker/beurs | [a_trade_q.yml](../.github/workflows/a_trade_q.yml) |
| [combiners.a_trade_combi](../combiners/a_trade_combi.py) | VCP/seizoensshortlist, fundamentele bevestigingen, gewogen score en ATR-filter | [a_trade_combi.yml](../.github/workflows/a_trade_combi.yml) |
| [combiners.shared](../combiners/shared.py) | Gemeenschappelijke selectieopschoning, relatieve scores, kosten en rapportopmaak | Geen zelfstandige workflow |

## Uitvoeren

```bash
python -m combiners.a_trade
python -m combiners.a_trade_q
python -m combiners.a_trade_combi
```

`--help` toont de modulebeschrijving zonder een scan te starten. De oude commando's `python a_trade.py`, `python a_trade_q.py` en `python a_trade_combi.py` zijn vervangen. Er staan geen uitvoerwrappers meer in de hoofdmap.

De workflowbestanden blijven rechtstreeks onder `.github/workflows/`. Hun namen, cron-expressies, secrets en rapportagekanalen zijn behouden. De aangepaste workflows controleren eerst hun module-import en voorkomen overlappende runs van dezelfde workflow op dezelfde branch. Voor Beste Signaal en Q is de joblimiet 30 minuten; voor de uitgebreide combinatiebot blijft die 240 minuten. Verschillende workflows kunnen nog naast elkaar draaien.

## Bevindingen en herstel

### Overlap en scores

- Herhaalde rijen geven geen extra bevestiging of extra gewicht aan de gemiddelde score.
- Per ticker/beurs en strategiefamilie telt de nieuwste selectie in het venster. Q gebruikt uitsluitend families die op de laatste datum van die ticker aanwezig zijn.
- Live/full-varianten van Hoogland en BeursSignalen tellen als dezelfde methode. Historische Graham- en Greenblatt-namen zijn eveneens samengevoegd.
- Uitvoer van `vers_signaal_1d`, `bot_combi_volatiel` en de `a_`-modellen wordt niet als nieuwe basisbevestiging meegeteld. Daarmee wordt een terugkoppellus voorkomen.
- Q blijft bewust de laatste datum **per ticker/beurs** gebruiken, niet één gezamenlijke datum voor alle tickers. Die datum wordt getoond en bij opslag behouden; een oud signaal wordt niet met de uitvoerdatum opnieuw vers gemaakt.
- Ruwe scores van verschillende strategieën hadden verschillende schalen. De gerapporteerde `avg_score` is nu het gemiddelde van relatieve posities per strategiefamilie, tussen 0 en 1. Ontbrekende scores en gelijke/onvergelijkbare referenties krijgen de neutrale waarde 0,5.
- Verschillende strategiefamilies zijn niet automatisch statistisch onafhankelijk. Veel methoden gebruiken dezelfde koers-, trend- of volumekenmerken. Overlap bewijst op zichzelf geen rendement.

`LOOKBACK_DAYS=3` betekent drie kalenderdatums inclusief vandaag. Toekomstige, ongeldige en oudere datums tellen niet mee. Op dezelfde dag wordt deterministisch een geldige koersbron gekozen; een oudere dag vervangt geen ontbrekende koers op de nieuwste signaaldatum.

### XGBoost

De oude code combineerde per ticker `MAX(score)` en `MIN(rang)` over meerdere dagen en mogelijk meerdere modelversies. Die waarden konden uit verschillende snapshots komen. Ook waren opgeslagen rangen batchgebonden.

Nu wordt één recente combinatie van modelversie en scoredatum gekozen voor de ingestelde horizon. Binnen die snapshot wordt de rang opnieuw berekend op basis van de opgeslagen score. Een ticker wordt alleen verrijkt wanneer zijn signaaldatum overeenkomt met die scoredatum. `XGB_MODEL_VERSIE` kan de modelkeuze expliciet vastzetten. Een fout bij het ophalen is zichtbaar als een mislukte run in plaats van stilzwijgend als ontbrekende verrijking.

### Koersen, kosten en budget

- De gekozen bronkoers hoort bij de getoonde signaaldatum en is positief en eindig. De bronvaluta/subeenheid wordt niet als euro gepresenteerd.
- Q schrijft geen selectie zonder geldige koers op die datum en controleert de opslagbevestiging.
- De kostenindicatie gebruikt het gedeelde `trade_cost`-model: `max(15, bedrag * 0.0035)`. `TOB_PCT` is een optioneel extra percentage; de standaard is nul, geen impliciete belastingaanname. Het is geen berekening van wettelijk verschuldigde belastingen.
- De indicatieve inzet per pick wordt begrensd door zowel `TRANSACTIE_BEDRAG` als het totale `BESCHIKBAAR_KAPITAAL`, inclusief de vermelde kosten. Bij vijf picks en €2.500 totaalbudget resulteert het standaardmodel bijvoorbeeld in maximaal €485 inzet plus €15 kosten per pick.
- Deze rapporten plaatsen geen orders, rekenen geen buitenlandse koersen om en bepalen geen aantal aandelen. Spreads, valutakosten en niet ingestelde belastingen zitten niet in de indicatie.

### Uitgebreide combinatiebot

- ATR14 wordt rechtstreeks uit geldige afgeronde OHLCV-dagen berekend. De bot roept daarvoor niet langer de volledige Kritische Selectie-analyse met dividendophaling en afgeronde koersen aan.
- ATR en VCP gebruiken dezelfde technische download. Downloads worden in batches van 100 tickers uitgevoerd, met voortgang en een request-time-out van 15 seconden. De afzonderlijke seizoensanalyse behoudt haar langere historie.
- Een ongeldige laatste koers wordt niet vervangen door een ouder signaal. Na recente datagaten wordt alleen de aaneengesloten bruikbare staart gebruikt.
- Verwacht ontbrekende seizoenshistorie wordt per ticker gemeld en overgeslagen; het breekt niet langer de gehele scan af.
- Kasstroom-, Fisher- en Hoogland-analyses worden per unieke ticker gecachet over overlappende beurslijsten, inclusief afwijzingen. De valutacache wordt waar beschikbaar gedeeld binnen die fase.
- Blijvende rate limits, een geheel mislukte opwarming of ontbrekende bruikbare ATR-data worden als fouten zichtbaar. De standaardopwarming is verkort tot twee pogingen met 15 seconden tussenpauze; herstel bij echte providerlimieten blijft bestaan.
- De oorspronkelijke trechter blijft behouden: een aandeel moet eerst via VCP of seizoenseffecten op de shortlist komen. Dit is dus geen zelfstandige fundamentele scan van alle tickers. Het gebruikt de bestaande x-lijsten.

### Dynamische gewichten

De verhouding gemiddelde/standaardafwijking van historische forward returns is een heuristiek, geen gevalideerde of geannualiseerde portefeuille-Sharpe-ratio.

De berekening aggregeert nu eerst per strategie en selectiedatum. `MIN_OBSERVATIES` (standaard 30) telt datums in plaats van losse tickerselecties. Een datum met veel selecties overheerst zo niet uitsluitend door het aantal rijen. Een conservatieve, horizonafhankelijke datumgrens beperkt de berekening tot gerijpte historie. Alleen bekende horizons worden in de SQL gebruikt.

Een strategie met voldoende bruikbare historie en een niet-positief gemiddeld rendement krijgt dynamisch gewicht nul. Positieve verhoudingen worden naar de ingestelde gewichtsband geschaald. Bij onvoldoende historie, onbruikbare spreiding of een mislukte gewichtenquery blijven de bestaande statische fallbackgewichten beschikbaar; dat wordt gelogd en in het rapport als mogelijkheid vermeld. Overlappende horizons en gedeelde marktbewegingen blijven afhankelijkheden veroorzaken. Ook deze gewichten moeten nog buiten de trainingsperiode worden geëvalueerd.

### Rapportage en workflows

De eerste twee workflows installeerden onvoldoende dependencies voor het geïmporteerde `common`-package. Pandas, NumPy en yfinance zijn toegevoegd. Fouten bij geconfigureerde notificatiekanalen en databaseopslag geven geen onjuiste succesmelding meer.

Herstel na de combi-run van 15 november 2026 (`2dac9d9`):

- Een geldige combi-scan zonder kandidaten verstuurt nu ook een Telegram-bericht, naast e-mail. Voorheen werd Telegram bij een lege topselectie overgeslagen.
- Yahoo kan 401/403/429-fouten alleen loggen en vervolgens onvolledige fundamentals teruggeven. De combi-bot bewaakt deze meldingen rondom de fundamentele analyses, ook wanneer een onderliggende strategie de fout opvangt. Toegangsweigering stopt de scan direct; rate limits blijven begrensd door de ingestelde retries. De bot publiceert bij zo'n bronfout geen nieuwe selecties.
- Als een uitgevoerde fundamentele strategie voor geen enkele shortlijstticker een bruikbare analyse oplevert, is de scan onvolledig. Een geldige analyse met een te lage score blijft wel een normale afwijzing. Individuele ontbrekende data zonder providerfout blijft een overslag.
- Bij een scan- of rapportagefout probeert de bot een `ONVOLLEDIG`-melding via Telegram en e-mail te sturen en blijft de uitvoering mislukt. Een fout bij het versturen verbergt de oorspronkelijke scanfout niet. De melding waarschuwt dat eventuele deelresultaten geen volledig rapport zijn.
- Fundamentele strategieën met gewicht nul worden overgeslagen om onnodige Yahoo-aanvragen te voorkomen. De technische shortlist blijft VCP én seizoenseffecten gebruiken, ook bij gewicht nul.
- De fix herstelt de foutafhandeling en rapportage; hij kan externe Yahoo-toegangsweigering niet opheffen. ATR-dekking blijft apart gerapporteerd en bewijst geen volledige fundamentele dekking.

Offline regressies controleren onder meer gelogde en opgevangen 401/429-fouten, volledig ontbrekende fundamentals, stoppen vóór databasepublicatie, lege én gevulde Telegram-rapporten, gewichten nul en notificatiefouten. Er is geen live scan gestart en geen testbericht verstuurd.

## Verificatie

- De volledige `tests/`-suite: 558 tests en 17 deeltests geslaagd, met negen overgeslagen verbindingstests. De bestaande bot- en opslagregressies blijven slagen.
- Nieuwe tests dekken onder meer duplicaten, eigen uitvoer, strategiefamilies, koersdatum, score-schaalinvariantie, kosten/budget, opslagbevestiging, datagaten, caching en gewichten.
- Alle drie de nieuwe modulecommando's zijn met `--help` uitgevoerd en de YAML-uitvoercommando's zijn gecontroleerd.
- De werkelijk gegenereerde XGBoost-snapshotquery is met meerdere datums en modelversies uitgevoerd in een geïsoleerde PostgreSQL-standalonebackend.
- Negen bestaande verbindingstests blijven overgeslagen omdat lokale databasesockets hier niet beschikbaar zijn. Er is geen live botscan gestart en er zijn bij deze herstructurering geen databasegegevens gewijzigd of berichten verstuurd.
