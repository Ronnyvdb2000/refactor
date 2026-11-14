# Controle MR EOD en ORB — 13 november 2026

Scripts/workflows:
- bot_00mr.py
- .github/workflows/run_00mrEOD.yml
- .github/workflows/run_00mrORB.yml

## Gevonden problemen

De EOD-scan boekte fictieve aankopen en verkopen tegen de signaalslotkoers,
combineerde buitenlandse koerseenheden tot een europortefeuille, telde iedere
herstart als extra houdduur en hield een arbitraire 10% belasting in. Een kapotte
state werd stilzwijgend vervangen door een standaardportefeuille. De workflows
pushten die state naar main, ook na mislukte runs, met een merge die conflicten
stil kon overschrijven. De ORB-modus boekte al geen orders, maar rapporteerde wel
budgetten en aantallen op basis van dezelfde onbetrouwbare portefeuille.

De eigen EOD-downloader miste de afzonderlijke herpogingen uit common. Bij de
laatste rij werd groupby.last gebruikt: daarmee kunnen niet-lege waarden uit
verschillende koersdagen in één indicatorrij worden gecombineerd. De ORB-scan
gebruikte de eerste beschikbare candle zonder te controleren of die overeenkwam
met de echte opening. De laatste candle kon nog lopen, tijdzones werden als
UTC-dag behandeld en signaalvolume zat in zijn eigen vergelijkingsgemiddelde.

## Adviesrapporten en portefeuillestate

Beide modi zijn nu expliciet adviesrapporten. Ze plaatsen geen brokerorders en
boeken geen fictieve transacties of kasmutaties. Het bestaande mr_portfolio.json
wordt niet overschreven, gereset of naar GitHub gepusht. In de aangetroffen state
waren geen open posities aanwezig. Er is geen poging gedaan het oude kassaldo
of eerdere performance financieel te valideren.

De EOD-modus leest eventuele bestaande MR-posities uitsluitend om de bestaande
IBS/MA20-exitvoorwaarden te tonen. Die adviezen worden niet als verkopen geboekt.
Een kapotte state wordt gemeld en leidt na rapportage tot een foutstatus.
Er worden geen totaalwaarde, dagverlies, budget, aandelenaantal, belasting of
performance voor een gemengde wereldwijde portefeuille geclaimd. Oorspronkelijke
koerseenheden kunnen ook subeenheden zoals pence zijn. Historische trade- en
snapshot-tabellen worden niet gewijzigd of gebruikt om performance te claimen.

## MR EOD

- De originele voorwaarden blijven: IBS < 0,20, RSI(3) < 10, slotkoers boven
  MA200 en R/R > 0,5. De stop ligt op Low - 0,5 × ATR; het referentiedoel is MA20.
  Alleen positieve, geordende stop/entry/doelniveaus zijn toegestaan.
- De downloader gebruikt common, inclusief herstel van gedeeltelijk ontbrekende
  batchdata. De scan valideert OHLCV, eindige positieve prijzen, volume,
  datumvolgorde, tijdzones en duplicaten.
- Minimaal 200 geldige afgeronde sessies zijn vereist. Vandaag en toekomstige
  koersdagen worden uitgesloten. De laatste koers mag maximaal zeven
  kalenderdagen oud zijn; recent volume mag niet volledig nul zijn. Dit is een
  eenvoudige daggrens, geen beurskalender. Na het slot kan bewust nog de vorige
  lokale sessie gebruikt worden.
- Elke ticker wordt eenmaal geanalyseerd. De werkelijke laatste indicatorrij
  wordt gebruikt; ongeldige waarden worden niet uit oudere dagen aangevuld.
- Alle berekeningen behouden hun precisie; afronding is uitsluitend presentatie.
  Signaaldata staan in CSV/JSON en e-mail. De volgende openingskoers is onbekend.

## ORB

De scan vraagt per ticker 15-minutendata zonder pre-/postmarket op en gebruikt
exchangeTimezoneName plus currentTradingPeriod.regular.start/end uit de
history-metadata van dezelfde yfinance-ticker. Unix-tijden en door de bibliotheek
opgemaakte tijdstempels worden beide verwerkt. Er worden geen vaste lokale
openingstijden voor alle aandelen verondersteld.

- Alleen een momenteel lopende reguliere sessie is actiegericht. Een gesloten
  beurs of wachten op de eerste twee complete candles is een normale status,
  geen downloadfout. Na het slot worden geen nieuwe ORB-adviezen gedaan.
- De openingscandle moet exact op de opgegeven reguliere opening beginnen.
  Een latere eerste beschikbare candle wordt nooit als vervanging gebruikt.
- Candles moeten afgerond zijn, op het 15-minutenrooster liggen en een
  aaneengesloten reeks vormen. Ongeldige OHLCV en candles buiten de sessie
  tellen niet mee. Een ontbrekende candle geeft een zichtbare datamelding.
- De laatste complete candle mag maximaal 30 minuten achterlopen op het
  moment van de scan. Dit is vertraagde signalering, geen realtime feed.
- Het volume wordt vergeleken met alle eerdere complete candles van dezelfde
  sessie; de signaalcandle telt niet mee in dat gemiddelde. Er wordt geen
  tijdstipgecorrigeerd historisch volumemodel geclaimd.
- Breakout: slot boven de openings-high op minimaal 1,5 × vergelijkingsvolume.
  De bestaande nabij-pivotvoorwaarde (boven 99,5% van openings-high) blijft een
  onbevestigde setup, ook wanneer het volume onvoldoende is voor breakout.
- Stop is de openings-low; doel is openings-high + 2 × openingsrange. Een
  koers op/boven dat doel of op/onder de stop geeft geen instapadvies.
- Signalen vermelden UTC-candlestart, sessieopening en bronkoerseenheid. De
  bronmetadata wordt niet onafhankelijk tegen een officiële beurskalender
  gecontroleerd. Halve handelsdagen en zomertijd volgen de aangeleverde
  sessiegrenzen; foutieve metadata kan daarom nog dekking beperken.
- Een ticker in overlappende lijsten wordt eenmaal gedownload en per beurs
  gerapporteerd. Alle tickers worden beoordeeld op één vast as-of-moment aan het
  begin van de run; downloaden van een groot universum kan merkbaar duren.

De ORB-rapportage is geen intraday order- of exitbewaking. De oude claim dat
posities gegarandeerd dezelfde dag gesloten werden is verwijderd.

Primaire bron voor metadata-ondersteuning:
https://github.com/ranaroussi/yfinance/blob/main/yfinance/utils.py
https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html

## Workflows en rapportage

Telegram blijft uit. Beide workflows installeren psycopg2-binary en gebruiken de
bestaande EMAIL_USER/PASS/RECEIVER en SUPABASE_DB_URL-secrets. De oude REST-client
supabase, SUPABASE_URL/KEY en state-pushstappen zijn niet nodig voor deze scans.
Selecties worden alleen via de bestaande db_logger opgeslagen.

De niet-bestaande menu-opties status/backtest zijn verwijderd. Er was in het
gecontroleerde script geen backtest-engine aanwezig, ondanks de oude koptekst.
CLI accepteert eod/live/orb; ongeldige modi falen expliciet.

De EOD-cron blijft 19:30 UTC op werkdagen. ORB liep voorheen om 07:30 UTC, voor de
reguliere opening van het gekozen universum. ORB draait nu op werkdagen om
10:00 en 15:30 UTC. Dat zijn in België 11:00/16:30 in de winter en 12:00/17:30 in
de zomer. Het zijn twee vaste momentopnamen, geen continue breakoutbewaking.
De sessiecontrole per ticker bepaalt uiteindelijk of een beurs open is.

Beide workflows hebben contents: read, voorkomen overlappende runs van hun eigen
modus en bewaren JSON/CSV zeven dagen als Actions-artifacts mr-eod-signals en
mr-orb-signals. Geen pushes, portefeuillemutaties of transacties vanuit de jobs.

Rapporten tonen dekking, ontbrekende/lege lijsten, dataproblemen, gesloten beurzen,
wachttijd en opgeslagen adviezen. Nul signalen levert ook e-mail en lege CSV met
vaste kolommen op. Gedeeltelijke dekking geeft [ONVOLLEDIG]. Analysefouten,
volledig onbruikbare data, opslag- en e-mailfouten laten de run mislukken.
JSON complete beschrijft scan/opslag, niet aflevering van de e-mail.

## Validatie en live hertest

40 offline regressietests voor MR-regels, laatste rij, precisie en stops,
OHLCV/tijdzones, originele sessieopening, incomplete/premarket/verouderde candles,
zomertijd, metadataformaten, ontbrekende candles, volume, doeloverschrijding,
deduplicatie, statebehoud, foutafhandeling en gesloten beurzen. Geen echte
berichten, database-inserts of mutaties aan de bestaande state tijdens de tests.

`python -m unittest discover -s tests -p 'test_00mr.py'`

Beide live hertests staan nog open:
- run_00mrEOD.yml → Run workflow → main
- run_00mrORB.yml → Run workflow → main

Controleer dekking, opslagbevestiging, ontvangen e-mails en artifacts. Een ORB-run
buiten beursuren kan correct nul actieve sessies/adviezen tonen. Dat valideert
nog niet de datalevering tijdens een daadwerkelijk geopende sessie.
