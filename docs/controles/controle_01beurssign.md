# Controle BeursSignalen — 14 november 2026

Repository: uitsluitend Ronnyvdb2000/refactor; refactor-lab niet gewijzigd.
Scripts/workflows: bot_01beurssign.py, run_01beurssign.yml (live/x-lijsten),
run_01beurssignfull.yml (full/a-lijsten). Eerdere controle_*.md geraadpleegd.

## Hersteld

- Telegram wordt niet meer geïmporteerd/aangeroepen; de onjuiste melding
  'Telegram verstuurd' is weg. E-mail en bestaande database-strategienamen blijven.
- Database-retourstatus/uitzonderingen worden gecontroleerd en in het rapport
  geteld. Opslagfouten, onverwachte analysefouten, geheel onbruikbare koersdata
  en mislukte e-mail geven een foutstatus. Een geldige lege selectie geeft wel
  e-mail en slaagt. Gedeeltelijke datadekking geeft [ONVOLLEDIG].
- Koersdata vereist Close, Volume en datums; minimaal 55 geldige afgeronde
  sessies. Lokale koersdatums blijven behouden, chronologie en duplicaten worden
  verwerkt. Vandaag/toekomstige datums worden uitgesloten. Laatste koers maximaal
  zeven kalenderdagen oud, positieve eindige Close en niet-negatief eindig volume.
  Ongeldige laatste waarneming wordt geweerd, niet vervangen door een oude koers.
  Oudere ongeldige rijen worden verwijderd; er wordt niets vooruit ingevuld.
  Recent handelsvolume en een bruikbare voorafgaande 20-sessiebaseline zijn vereist.
- Signaaldag telt zoals voorheen niet mee in het vergelijkingsvolume. Score,
  koers en ratio's blijven nu ongerond tot presentatie. Ranking is deterministisch
  op scorefractie, volumeratio en ticker. Koersdag staat in e-mail en database;
  datum in selecties is nu de signaaldatum, niet de scriptstartdatum.
- Insiderberekening gebruikt uitsluitend Shares, nooit Value (geldbedrag).
  Alleen herkenbare koop/verkooptransacties binnen 90 dagen en niet in de toekomst
  tellen mee. Tijdzones worden op UTC afgestemd. Verkoopaantallen worden als
  absolute aantallen afgetrokken. Ontbrekende/onbruikbare aantallen, dubbelzinnige
  labels of ontbrekende transacties geven onbekend, geen fictieve nul.
  Onbekende insiderdata staat bij databeperkingen en geeft geen extra scorepunt.
- Een ticker wordt eenmaal per beursclassificatie (VS/niet-VS) geanalyseerd,
  inclusief caching van missers. Overlap blijft per beurs zichtbaar/opgeslagen.
  Volledige kandidaatcount is gescheiden van de begrensde topselectiecount.
  Ook bij nul kandidaten toont de topsectie eventueel lagere scores expliciet
  als onder de selectiedrempel. Lege/ontbrekende lijsten worden gemeld.
- Beide workflows behouden planning, Python 3.11, e-mail- en databasesecrets.
  PYTHONUNBUFFERED toont voortgang direct; concurrency voorkomt overlap van
  dezelfde workflow op dezelfde branch. De twee modi mogen naast elkaar draaien.
  Alleen contents: read, geen workflow-push. CLI accepteert uitsluitend live/full;
  ongeldige modi starten niet meer stilzwijgend een live scan. De oude backtest
  deed geen berekening en is niet langer als bruikbare modus aangeboden.

## Modelgrenzen

De bestaande criteria/drempels blijven: boven VWAP20 en VWAP50, maximaal 8%/6%
boven VWAP20, overheadpercentiel 75/70, volume minimaal 1,5x/1,8x; minimumscore
3, top 5/3 (live/full). Niet-VS maximaal 3 punten; VS maximaal 4 inclusief insiders.
VS-classificatie blijft op beurslijstnummer 048/055/056/057 gebaseerd, niet op
onafhankelijk geverifieerde ondernemingsdomicilie of SEC-dekking.

Dit is een eigen heuristiek. 'VWAP' is een volumegewogen gemiddelde van dagelijkse
slotkoersen, geen intraday VWAP. Overhead is een histogram van slotkoersen en
volume over maximaal de laatste 60 sessies; 55 sessies blijven toegestaan.
Geen verandering naar een andere strategie of gevalideerde prestatieclaim.
Yahoo-data en insiderlabels worden niet onafhankelijk geverifieerd. Geen
publicatiedatummodel of historische backtest van insiderinformatie. Onbekend is
geen bewijs van afwezigheid van insiderhandel. Geen orders of portefeuilleboekingen.
Koersen blijven bronkoerseenheden (mogelijk pence), zonder FX-conversie.
De daggrens en zevendagengrens zijn geen specifieke beurskalender; ook na het
slot wordt vandaag conservatief overgeslagen. Verwijderde slechte oudere rijen
kunnen gaten in de gebruikte sessies achterlaten.

## Validatie

28 nieuwe offline regressietests geslaagd, plus 40 bestaande MR-tests.
Yahoo, database en e-mail gemockt; geen echte meldingen of database-inserts.
Beide workflows geparseerd, CLI/compile en git diff --check gecontroleerd.

```
python -m unittest discover -s tests -p 'test_01beurssign.py' -q
python -m unittest discover -s tests -p 'test_00mr.py' -q
python -m py_compile bot_01beurssign.py
```

Live hertests van BeursSignalen staan nog open voor beide workflows op main.
Controleer datadekking, onbekende insiderdata, koersdatums, opgeslagen aantallen
en ontvangen e-mail. Tijdens deze controle zijn geen live runs gestart.

## Overgedragen herteststatus

Volgens de gebruikersoverdracht zijn O'Shaughnessy, Supervar, VCP en xxxV2
technisch live geslaagd, met gemelde databeperkingen. Deze runs zijn hier niet
opnieuw uitgevoerd/gevalideerd. MR EOD en ORB na commit e77d85b staan nog open;
ORB vereist ook een test tijdens daadwerkelijk geopende beursuren.


## Live x-lijsten en herstel laatste Europese koers — 14 november

Aangeleverde live-run op commit 6d99f5a is technisch geslaagd: 446/1000 unieke
gevraagde tickers geanalyseerd, 3/3 selecties opgeslagen, e-mail bevestigd en
Telegram uit. Dekking was onvoldoende: vrijwel heel Europa werd geweerd wegens
een ongeldige laatste koers. Dit is geen inhoudelijk afgeronde wereldwijde scan.

Rechtstreeks gereproduceerd met yfinance 1.8.0 voor ASML.AS en AIR.PA: op
13 november waren slotkoers en adjusted close NaN terwijl volume aanwezig was.
Auto-adjust maakte daardoor ook Open/High/Low NaN. Zonder auto-adjust waren
Open/High/Low aanwezig, Close nog steeds NaN. Een heraanvraag met repair=True
herstelde dezelfde sessie voor beide tickers. Dit bevestigt bron-/herstelgedrag;
het valideert niet onafhankelijk de door Yahoo gereconstrueerde slotkoersen.

Bij een ongeldige laatste koers/volume doet de bot nu één gerichte heraanvraag
met repair=True. Alle validaties blijven gelden; dezelfde laatste koersdag moet
terugkomen. Oudere data is geen vervanging voor een nog steeds ongeldige laatste
sessie. Datum, Close en Volume staan nu in de foutmelding. Herstel wordt gelogd,
in de databeperkingen gerapporteerd en als data_note bij selecties opgeslagen.
Ook een geslaagde herstelde analyse houdt daarmee een [ONVOLLEDIG]-markering.
Geen wijziging van scoredrempels, geen gefabriceerde prijzen of forward-fill.

32 offline tests geslaagd (vier nieuwe hersteltests), plus twee echte read-only
Yahoo-analyses: ASML.AS en AIR.PA kregen beide koersdag 2026-11-13 terug na herstel.
Geen e-mail of database-insert bij die twee controles. Nieuwe workflowruns voor
x- én a-lijsten zijn nodig om de volledige dekking na dit herstel te beoordelen.
Een nog lopende run met de eerdere commit krijgt de fix niet automatisch mee.
