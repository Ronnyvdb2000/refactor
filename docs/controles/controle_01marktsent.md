# Controle Marktsentiment — 14 november 2026

Repository uitsluitend Ronnyvdb2000/refactor; refactor-lab apart.
Script bot_01marktsent.py, workflow run_01marktsent.yml. Eerdere controleverslagen
in docs/controles geraadpleegd. Geen gedeelde helper of andere bot aangepast.

## Live koersanalyse

- Iedere unieke ticker wordt één keer technisch geanalyseerd, ook bij overlap
  tussen beurslijsten. De tweede volledige indicatorberekening voor nieuws is
  vervangen door het toevoegen van sentiment aan hetzelfde technische resultaat.
  Een ticker kan per beurs gerapporteerd/opgeslagen blijven.
- OHLCV, eindige positieve prijzen, samenhangende high/low, niet-negatief volume,
  datums, chronologie en duplicaten worden gevalideerd. Er is geen verzonnen
  high/low gelijk aan Close meer. Minstens 210 geldige sessies zijn vereist.
- Lokale koersdatums blijven behouden. Vandaag en toekomstige datums worden
  uitgesloten. De laatste bruikbare koers mag maximaal zeven kalenderdagen oud
  zijn en recent handelsvolume is vereist. Een ongeldige laatste OHLCV-rij in de
  aangeboden historie wordt geweerd; oudere ongeldige rijen worden verwijderd
  en geteld. Geen forward-fill van indicatorinputs.
- De gedeelde downloader en afzonderlijke retry blijven behouden. Die downloader
  kan al ongeldige Close-rijen verwijderen vóór de lokale validatie. Dit is dus
  geen garantie dat de nieuwste leverancierssessie volledig aanwezig was.
  Het rapport toont per beurs het bereik van werkelijk gebruikte koersdatums.
  De Yahoo-repair van BeursSignalen is niet naar deze bot of common uitgebreid.
- Alle gebruikte indicatoren en referentiestops moeten eindig zijn; ATR en stop
  moeten positief zijn en stop lager dan koers. Volledige precisie blijft behouden.

De vier oorspronkelijke technische punten/drempels blijven: RSI(14) <=35 of
55–70, opwaartse MACD-cross in de laatste vijf intervallen, Close boven SMA200,
en maximaal 15% boven SMA50. Die laatste voorwaarde kan zoals voorheen ook onder
SMA50 gelden; er is geen extra ondergrens toegevoegd. MACD blijft 12/26/9,
ATR(14), stop Close - 2 ATR. Dit zijn referentieniveaus, geen orders.

Het historische veld rsi_monthly blijft voor databasecompatibiliteit bestaan,
maar bevat in deze bot altijd RSI op dagelijkse bars. rsi_daily wordt daarnaast
in JSON opgeslagen; de rapportage noemt dagelijkse RSI expliciet.

## Nieuws en budget

- Eén NewsAPI-aanvraag per unieke mogelijke kandidaat. Technische scores <3
  kunnen minimumscore 4 met maximaal +1 nieuwsbonus niet bereiken en verbruiken
  daarom geen nieuwsbudget meer. Score 3 krijgt zoals eerder grensgevallen
  voorrang boven score 4; gelijke technische scores sorteren nu op ticker.
- De bestaande bonusformule blijft: compound >=0,15 geeft die positieve waarde
  (maximaal 1), <=-0,15 die negatieve waarde (minimaal -1), anders nul. Er is geen
  automatische hele punt bij ieder positief bericht. Score 3 haalt dus alleen
  de drempel bij bonus 1; positieve maar lagere bonus is onvoldoende. Negatief
  nieuws kan een technische 4 alsnog onder minimumscore 4 brengen.
- De grens 95 is expliciet per procesrun. Budget en blokkadestatus resetten aan
  het begin van een nieuwe run. Er is geen persistent/gedeeld dagquotum; meerdere
  handmatige runs kunnen het providerdagquotum nog bereiken.
- Artikelen moeten een geldige publicatiedatum in het zeven-dagenvenster tot het
  vaste UTC-startmoment van de scan hebben. Lege/[Removed]-teksten, ongeldige
  VADER-scores, dubbele titels of dubbele URL's tellen niet mee. Minimaal twee
  werkelijk bruikbare unieke artikelen zijn vereist, niet twee ruwe API-records.
- De sleutel gaat als X-Api-Key-header mee, niet in queryparameters. Ruwe
  request-uitzonderingen/antwoorden worden niet gelogd om sleutel- of URL-lekken
  te vermijden. HTTP-fouten, onleesbare antwoorden, ontbrekende sleutel,
  onvoldoende artikelen en uitgeput budget krijgen een zichtbare status.
- Na HTTP 429 worden verdere aanvragen overgeslagen. Ook HTTP 401/403/426 stopt
  nieuwe aanvragen in dezelfde run; herhalen met dezelfde sleutel/plan heeft
  dan geen zin. Andere tijdelijke fouten worden per ticker zichtbaar.
- Ontbrekend nieuws blijft onbekend met bonus nul. De technische scan kan dan
  doorgaan met [ONVOLLEDIG], zonder neutraal nieuws als geobserveerd sentiment
  voor te stellen. Het oplossen van bedrijfsnamen wordt overgeslagen als geen
  aanvraag meer mogelijk is; de bedrijfsnaamcache is per run.

Dit blijft een Engelse, lexicongebaseerde VADER-heuristiek over maximaal de
nieuwste twintig aangeleverde artikelen. Relevantie voor de onderneming,
volledigheid, syndicatieduplicaten met afwijkende titel/URL en providervertraging
worden niet onafhankelijk gecontroleerd. Nieuws is van het scanmoment, terwijl
de technische koers van de expliciet genoemde afgesloten sessie is. Dit is
geen historisch verhandelbaar sentimentmodel of koersvoorspelling.

## Rapportage, opslag en workflow

Telegram wordt niet geïmporteerd of aangeroepen. E-mail, SUPABASE_DB_URL en
NEWSAPI_KEY blijven behouden. Lege kandidatenlijsten geven ook een e-mail.
Rapporten bevatten datadekking, nieuwsstatus/budget, ontbrekende lijsten en
werkelijk geslaagde database-inserts. Gedeeltelijke data/nieuws geeft
[ONVOLLEDIG]; geen bruikbare technische analyses, onverwachte analysefouten,
opslagfouten en mislukte e-mail geven een foutstatus.

Per beurs worden maximaal vijftien signalen gerapporteerd én opgeslagen. Voorheen
werden boven die weergavelimiet wel extra selecties in de database gezet.
Kandidaatcount vóór de limiet en topselectiecount staan afzonderlijk in het rapport.
Ranking blijft op totale score dalend, nu met ticker als deterministische tie.
Database-identificatie bot_01marktsent blijft. Datum is nu signaalkoersdatum;
news_asof_utc bewaart afzonderlijk het nieuwsreferentiemoment.

Koersen en stops staan in oorspronkelijke bronkoerseenheden, mogelijk pence;
geen vaste EUR-labels of fictieve positiegroottes. De workflow behoudt planning,
Python 3.11, live-modus, contents: read en bestaande secrets. PYTHONUNBUFFERED,
timeout 180 minuten en concurrency per workflow/branch zijn toegevoegd. Geen
workflow-pushes of live brokerorders. CLI accepteert live/backtest expliciet;
een ongeldige modus start niet stilzwijgend live.

## Technische backtest

De oude backtest hield handelsdagen bij als maandelijkse scans, controleerde
stops alleen op die maandmomenten, stapte op de signaalslotkoers in, rekende
instapkosten over een bedrag inclusief kosten en telde verschillende valuta
als één europortefeuille. Ook de ingestelde start/einddatum werden niet gebruikt.

De vervangende simulatie gebruikt een afzonderlijke rekening per ticker met
50.000 oorspronkelijke koerseenheden. Er is geen gezamenlijk portefeuille-
vermogen, geen tienpositieslimiet over meerdere valuta en geen optelling tot
EUR-rendement. Binnen iedere ticker is maximaal één positie open. De vaste
kosten van common zijn modelaannames in die koerseenheden en daardoor niet
voor alle valuta economisch gelijkwaardig.

- Download van start minus twee kalenderjaren tot en met de gevraagde einddatum.
  Minstens 210 geldige opwarmsessies vóór start zijn vereist; anders wordt de
  ticker zichtbaar overgeslagen. Er is geen aanvulling met een latere IPO.
- Op de eerste beschikbare sessie van iedere gesimuleerde maand wordt na het slot
  de bestaande technische score getoetst; de eerste sessie binnen de gevraagde
  periode kan bij een start midden in een maand het eerste scanmoment zijn.
  Er wordt uitsluitend historie tot die sessie gebruikt, zonder nieuws.
- Instap op de volgende beschikbare opening, maximaal zeven kalenderdagen later.
  Opening op/onder de referentiestop geeft geen instap. Instapslippage en kosten
  gebruiken werkelijke modelnotional. De stop uit de signaaldag blijft vast.
- De integer positiegrootte past binnen cash én 5% modelverlies bij stop,
  inclusief instap-/uitstapkosten en slippage. Geen minimum van één aandeel
  wanneer dat het budget zou overschrijden. Een gap kan meer verlies geven.
- Iedere beschikbare bar controleert de stop, inclusief de instapdag. Een gap
  door de stop gebruikt de opening, anders de stop; vervolgens verkoopsippage
  en werkelijke verkoopkosten. Ontbrekende bars worden niet gereconstrueerd.
- Na zestig complete geobserveerde positiesessies volgt een tijdsexit op de
  eerstvolgende opening. De trade-log telt de uitstapsessie mee; dat kan 61
  sessies inclusief de uitvoering zijn. Geen telling van scriptstarts/maanden.
- Equity gebruikt iedere laatste beschikbare slotkoers en de beginwaarde telt
  mee in drawdown. Open posities worden gemarkeerd en gewaardeerd zonder
  fictieve sluitingskosten. Instapkosten zijn wel geboekt. Geen willekeurige
  10% belastinginhouding. Zonder verliestrades blijft profit factor onbekend.

Uitvoer, ook zonder gesloten trades: backtest_results/marktsent_summary_per_ticker.csv,
marktsent_trades.csv en marktsent_equity.csv. De samenvatting noemt open posities
en laatste koersdatum. De standaardworkflow blijft uitsluitend live; lokale
backtestuitvoer wordt niet automatisch vanuit die live-workflow gepubliceerd.
Geen brokeruitvoering, FX-model, point-in-time koersreconstructie of bewezen
rendement. Actuele tickerlijsten bevatten survivorship bias; aangepaste data en
barfills zijn modelaannames. De oude gezamenlijke portefeuille-uitkomsten zijn
niet vergelijkbaar met deze afzonderlijke tickerrekeningen.

## Verificatie en vervolg

48 offline tests geslaagd voor: originele technische regels, stop- en OHLCV-
validatie, datums/tijdzones, nieuwsminimum/duplicaten/publicatiedatums,
VADER-bonus, foutantwoorden en budget, deduplicatie, rapportlimiet en opslag,
lege/gedeeltelijke uitkomsten, verzend-/opslagfouten, volgende-openinginstap,
dagelijkse/gap-/instapdagstops, zestig sessies, cash/risico/kosten, open waardering,
begindrawdown, datumbereik en lege CSV's. Workflow is geparseerd en gecontroleerd.

```
python -m unittest discover -s tests -p 'test_01marktsent.py' -q
python -m py_compile bot_01marktsent.py
```

In een nieuwe lokale Python 3.12-omgeving met uitsluitend de workflowpakketten
slagen pip check, botimport, technische analyse op synthetische koersdata en de
echte VADER-analyzer. Actions blijft Python 3.11. Geen echte Yahoo/NewsAPI-
aanvragen, e-mails, database-inserts of brokerorders bij deze verificatie.
Een volledige historische backtest is niet uitgevoerd; compile, CLI-help en
git diff --check slagen.

Live hertest staat open via run_01marktsent.yml op main. Controleer beschikbare
NewsAPI-sleutel/plan, bruikbare artikelen versus technische fallback, koersdatums,
quota, datadekking, opgeslagen aantallen en ontvangen e-mail. 0/0 inserts bij
geen signalen valideert geen echte databaseopslag.

De lopende andere workflows zijn niet herstart of geannuleerd. Volgende bot in
bestandsvolgorde: bot_01repititief.py.
