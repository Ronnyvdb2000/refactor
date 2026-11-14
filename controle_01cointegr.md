# Controle Pairs Trading — 14 november 2026

Repository uitsluitend Ronnyvdb2000/refactor. Script bot_01cointegr.py;
workflow run_01cointr.yml. Refactor-lab blijft apart.

## Selectie en data

- Ontbrekende koersen worden per paar uitgelijnd. De oude dropna over alle
  tickers kon alle paren reduceren tot de korte historie van één nieuwe notering.
  Beide benen moeten op hun eigen laatste beschikbare koersdag aanwezig zijn;
  oude gezamenlijke koersen vervangen geen ontbrekende laatste koers.
- Dagdata is chronologisch, uniek en numeriek, met eindige positieve koersen.
  Vandaag en toekomstige datums worden uitgesloten; lokale koersdatums blijven
  behouden. Live vereist minstens 60 geldige sessies en maximaal zeven dagen
  oude laatste koersen. Een ongeldige laatste koers wordt zichtbaar geweerd.
  Oudere ongeldige rijen worden verwijderd zonder vooruitvullen voor signalen.
- Downloads blijven in batches van 100 met pauzes. De gedeelde downloader
  herstelt ook ontbrekende tickers uit gedeeltelijk geslaagde batches afzonderlijk.
- P-waarden, ratio's, half-life en prijzen blijven ongerond tot presentatie.
  Benjamini-Hochberg gebruikt alle geteste paren na de bestaande absolute
  koerscorrelatievoorfilter van 0,7; half-life <=30 wordt pas daarna toegepast.
  Niet-berekenbare statistische toetsen tellen conservatief met p=1 mee.
  Ongeldige p-waarden worden niet significant. Ties sorteren op pairnaam.
- Ratio-spread A/B, lookback 60, entry +/-2, exit +/-0,5, FDR-alpha 0,05 en
  de oorspronkelijke half-life-benadering blijven behouden. Engle-Granger toetst
  koersniveaus; de handelsheuristiek gebruikt de ratio en geen OLS-hedgeratio.
  Dit verschil wordt niet als een nieuwe strategie herschreven. Correlatievoorselectie
  en afhankelijke toetsen betekenen dat dit geen bewezen universele FDR-garantie
  is. De koersbron en economische geschiktheid van een paar zijn niet geverifieerd.

## Live rapport en workflow

Telegram wordt niet meer geïmporteerd of aangeroepen; workflowsecrets blijven
uitgeschakeld. E-mail en database blijven actief. Ook nul signalen geeft e-mail.
Ontbrekende lijsten/tickers worden gemeld, gedeeltelijke dekking als [ONVOLLEDIG].
Onbruikbaar universum, analysefouten, opslagfouten en e-mailfouten geven een
foutstatus. Opslag bevestigt het werkelijk aantal geslaagde inserts.

Actieve signalen worden vóór de top-5-limiet geselecteerd: neutrale paren nemen
geen actieve plekken meer in. Alle maximaal vijf getoonde signalen worden
opgeslagen; de oude database-opslag beperkte zich ten onrechte tot drie.
Koersdatum staat in rapport en database en wordt als selectiedatum gebruikt.
LONG betekent long A/short B; SHORT het omgekeerde. Koersen blijven oorspronkelijke
bronkoerseenheden; adviezen zijn geen orders. Shortbaarheid wordt niet gecontroleerd.

Workflow behoudt planning en Python 3.11, krijgt contents: read, timeout 300 minuten,
concurrency per workflow/branch en directe loguitvoer. De onnodige git pull met
conflictvoorkeur is verwijderd: checkout blijft de bron voor de run. Geen pushes.
Backtest-CSV's worden zeven dagen als artifact cointegr-backtest bewaard.
CLI weigert ongeldige modi in plaats van stilzwijgend live te draaien.

## Backtest: synthetische simulatie

De oude simulatie combineerde verschillende noteringsvaluta tot EUR-vermogen.
Nu wordt elk been met uitsluitend data vóór de gevraagde startdatum naar 100
herbaseerd. Cash, allocatie, kosten en P/L zijn expliciet synthetische indexeenheden;
geen werkelijk belegbaar eurovermogen of EUR-rendement. Een been vereist 252
opwarmsessies vóór de gevraagde start. Download gebruikt start minus twee jaar
tot en met de einddatum, niet een verschuivend 5y-venster dat de gevraagde start mist.
Ontbrekende historie kan de daadwerkelijke start beperken; lege data/periode faalt.

Bestaande walk-forwardselectie (252 in-sample, herselectie elke 20 sessies), vijf
paren per beurs, maximaal twintig open paren en 10% van vrije cash per pair blijven.
Selecties en dagelijkse z-scores gebruiken data tot de vorige sessie. Uitvoering
blijft op de volgende beschikbare dagslotkoers, geen gegarandeerde opening.
Houdduur telt gezamenlijke geobserveerde pair-sessies sinds instap, niet
scriptstarts of dagen zonder koersen. Maximaal 60 van die intervallen; ontbrekende
uitvoeringskoersen kunnen de daadwerkelijke exit vertragen.

Instap/uitstap met richtingafhankelijke 0,1% slippage en gedeelde kosten over
werkelijk modelnotional. De willekeurige 10% belasting is verwijderd. Vrij cash
plus marge plus model-P/L vormt equity. Bij ontbrekende actuele koersen gebruikt
waardering de laatst bekende koers; dit kan verouderde waardering zijn en is
geen uitvoeringsprijs. Equity is een liquidatieschatting inclusief slippage en
verwachte sluitingskosten. Die worden niet als uitgevoerde trades geboekt.
Drawdown bevat de beginwaarde; profit factor zonder verliezen is onbekend.

Dit is een synthetisch model: geen FX-, financierings-, borrow-, short availability-,
realistisch broker-marge- of margincallmodel. De vaste kosten in indexeenheden
zijn modelaannames. Gedeelde legs tussen paren kunnen bruto blootstelling stapelen.
Geen claim van historische realiseerbaarheid. Actuele tickerlijsten veroorzaken
survivorship bias; aangepaste koersen zijn geen point-in-time reconstructie.

Uitvoer ook zonder gesloten trades: backtest_results/backtest_cointegr.csv,
cointegr_equity.csv en cointegr_open.csv. Open posities blijven expliciet zichtbaar.

## Validatie en vervolg

27 nieuwe offline tests geslaagd, plus 28 BeursSignalen-tests. Getest: pairwise
kalender, nieuwste gezamenlijke sessie, precisie, FDR-familie en half-lifevolgorde,
koersvalidatie, downloadbatching, LONG/SHORT, top-5-opslag, lege selectie,
database/e-mailfouten, start/eindvenster, volgende-sessie-uitvoering, sessiehoudduur,
kosten/cashaansluiting, open posities en workflowinstellingen.

```
python -m unittest discover -s tests -p 'test_01cointegr.py' -q
python -m unittest discover -s tests -p 'test_01beurssign.py' -q
python -m py_compile bot_01cointegr.py
```

Geen externe meldingen of database-inserts tijdens de tests. Live hertest staat
open via run_01cointr.yml, mode live, main. Controleer looptijd, datadekking,
paren, koersdatums, opslag en ontvangen e-mail. Geen live run of grote historische
backtest gestart. MR EOD en ORB blijven open; ORB ook tijdens beursuren testen.
Volgende bot in bestandsvolgorde: bot_01hoogl.py.
