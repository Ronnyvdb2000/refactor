# Controle GLOBAL ENGINE xxx V2 — 13 november 2026

Workflow: `.github/workflows/run_00ultxxxV2.yml`.
Actions-naam: **Bot 00: FULL Scanner (041-059) xxx V2**.
Script: `bot_00xxxV2.py`.

## Probleem en herstel

De workflow gaf geen e-mail- of databasegegevens door, installeerde geen
PostgreSQL-driver en draaide op Python 3.9. De bot stuurde uitsluitend Telegram,
terwijl Telegram in de workflow niet geconfigureerd was. Hij wijzigde wel lokale
portefeuille-CSV's, zonder dat de workflow die state duurzaam bewaarde.
De selectie probeerde een onbekende volgende opening als actuele entry te
gebruiken. NaN is truthy in Python, waardoor `next_open or close` dat niet veilig
oploste. Herstarts verhoogden de houdduur opnieuw. Bedragen van verschillende
noteringen werden zonder valutaomrekening als europortefeuille behandeld.

De workflow gebruikt nu Python 3.11, de database-driver en bestaande e-mail- en
databasesecrets. Telegram blijft ongeconfigureerd. De live scan schrijft geen
portefeuille-state en voert geen transacties uit. Hij genereert per ticker
adviezen met de laatste beschikbare afgeronde koersdag. De echte volgende
opening is op dat moment onbekend en wordt niet als feature gebruikt.

## Live advies

De zes bestaande substrategieën en ATR-multipliers blijven behouden:
Traag, Snel, Hyper Trend, Hyper Scalp, MRA Snel en MRA Traag. Elke strategie
houdt haar oorspronkelijke voorwaarden. Er wordt geen strategie verwijderd
omdat de kas van een fictieve gemengde portefeuille onvoldoende zou zijn.

Iedere unieke ticker wordt eenmaal geanalyseerd. De bot valideert OHLCV,
chronologie, tijdzones, dubbele datums en eindige positieve prijzen. Hij vereist
200 geldige afgeronde sessies, een laatste koers van maximaal zeven kalenderdagen
oud en recent handelsvolume. Vandaag en toekomstige datums worden uitgesloten.
Dit is een eenvoudige daggrens, geen beurskalender. De downloader behoudt zijn
bestaande afzonderlijke herpogingen bij ontbrekende batchdata. Er is geen
verborgen fallback naar een kleine lijst Amerikaanse/Benelux-aandelen meer.

Per beurs worden maximaal tien strategie-signalen gerapporteerd en opgeslagen,
zoals de oorspronkelijke rapportlimiet beoogde. De volgorde is R/R dalend,
daarna ticker en strategienaam. Meerdere signalen voor dezelfde ticker zijn
mogelijk; dit zijn onafhankelijke adviezen, geen belegbare verdeling over tien
posities. Overlap tussen beurslijsten blijft per beurs zichtbaar. De gelijke
R/R van veel ATR-strategieën is grotendeels door de modelmultipliers bepaald.
Die verhouding is geen voorspelde opbrengst en geen kwaliteitsrang van bedrijven.

Referentie-SL/TP zijn gebaseerd op slotkoers en ATR. Er worden geen eurobedragen,
aandelenaantallen of kasbeschikbaarheid geclaimd voor een wereldwijde portefeuille.
De koerseenheid kan ook een subeenheid zoals pence zijn. De datum van het signaal
staat in rapport, CSV en database. Stop en doel zijn referentieniveaus, geen orders.

Als een lokale `live_state.json` bestaat, worden de posities alleen gelezen voor
verkoopadviezen. De houdduur wordt afgeleid uit de beschikbare unieke koersdagen
sinds de instap; opnieuw scannen telt geen dagen extra. Een verkoopadvies wordt
niet als uitvoering geboekt. Als een bestaande positie door een tekort aan
historie niet analyseerbaar is, staat die ticker bij de ontbrekende data. Een
onleesbare state geeft een foutmelding; hij wordt nooit stilzwijgend gereset.
De Actions-workflow importeert geen lokale portefeuillestate.

Ook nul signalen levert een e-mail, JSON en CSV met vaste kolommen op. Datadekking,
ontbrekende lijsten, analysefouten en succesvolle database-inserts zijn zichtbaar.
Gedeeltelijke dekking wordt gemarkeerd als [ONVOLLEDIG]. Geheel onbruikbare data,
analysefouten, opslagfouten en mislukte e-mail laten de run mislukken.

## Handmatige simulatie: alleen via `apply`

`apply` verwerkt een lokaal commands.txt, zonder Telegram-ontvangst of brokerorders.
`/buy TICKER PRIJS AANTAL` en `/sell TICKER PRIJS AANTAL` zijn ondersteund.

- Accountvaluta is expliciet één koerseenheid via PORTFOLIO_CURRENCY, standaard
  USD. Yahoo moet voor alle betrokken/bestaande posities dezelfde koerseenheid
  bevestigen. Geen valutaconversie of combinaties van GBP en GBp.
- Prijs en aantal moeten positief/eindig zijn, de kas mag niet negatief en het
  aantal posities niet boven tien. Een dubbele koop overschrijft geen positie.
- Een verkoop verkoopt het opgegeven aantal, niet automatisch de hele positie.
  De resterende instapkosten worden evenredig verdeeld. De opgegeven handmatige
  prijs is de uitvoeringsprijs; er wordt geen tweede slippage op toegepast.
- Eerst wordt de hele batch op een kopie gevalideerd. Onjuiste regels verhinderen
  de hele batch en blijven in commands.txt staan.
- `live_state.json` is de gezaghebbende state met kas, posities, trades en hashes
  van verwerkte commandobestanden. Hij wordt atomisch vervangen. De CSV's zijn
  opnieuw op te bouwen projecties, geen afzonderlijke bron van waarheid.
- Commands.txt wordt pas na een geslaagde opslag geleegd. Na een onderbreking
  herkent een herstart dezelfde bestandsinhoud, ook over middernacht, en voert die
  niet opnieuw uit. Bij een identieke nieuwe bedoelde batch: voeg een unieke
  commentaarregel toe, bijvoorbeeld `# batch 2026-11-20-1`. Comments tellen mee in
  de bestandshash. Verwijder geen hashes om een onzekere vorige batch te herhalen.
- Gelijktijdig uitvoeren van meerdere lokale apply-processen wordt niet
  ondersteund. Eerdere CSV-state zonder JSON wordt niet automatisch gemigreerd:
  controleer eerst valuta, kas, posities en transacties. In de repository waren
  zulke statebestanden of commands.txt niet aanwezig tijdens de controle.

Een waarde voor alle posities wordt alleen berekend als actuele waarderingsprijzen
voor elke positie beschikbaar zijn. Ontbrekende prijzen worden niet als actuele
instapkoers voorgesteld. De CSV-projectie kan daarom een leeg totaalveld hebben.
Er is geen automatisch belastingmodel; de eerdere willekeurige 10% is verwijderd.

## Backtest

Elke ticker heeft nu een afzonderlijk account met 50.000 oorspronkelijke
koerseenheden. Resultaten worden niet als gezamenlijk EUR-portefeuillerendement
opgeteld. De zes strategieën delen binnen één ticker dat account en maximaal één
positie. Bij gelijktijdige signalen wordt de eerste volgens dezelfde vaste
R/R/ticker/strategienaam-sortering gekozen. Er zijn geen zes onafhankelijke
strategie-backtests; de strategie-CSV beschrijft gerealiseerde trades uit die
gezamenlijke selectie binnen de ticker.

- Expliciete start/einddatums met twee jaar aanloopdata. De runner vereist
  200 sessies vóór de start; nieuwe noteringen worden daarmee uitgesloten.
- Slotkoerssignalen worden pas op de volgende beschikbare opening uitgevoerd,
  maximaal zeven dagen na het koopsignaal. Instapdatum en kasboeking horen bij
  die uitvoeringsdag. Er bestaat geen terugval naar de signaalslotkoers.
- Zoals het oorspronkelijke ATR-model worden SL/TP bij uitvoering opnieuw
  bepaald vanaf de echte modelentry en de ATR van de signaaldag. Een gap kan
  daardoor andere niveaus geven dan het live referentieadvies.
- Stop/doel worden elke beschikbare sessie met Open/High/Low gecontroleerd.
  Gaps gebruiken de opening; raken stop en doel binnen één bar, dan gaat de
  stop conservatief voor. Ook de instapdag kan een stop/doel raken.
- Trend-, RSI- en tijdsexits ontstaan na het slot en worden op de volgende
  beschikbare opening uitgevoerd. Twintig dagen betekent twintig waargenomen
  sessies, niet twintig scriptstarts. Dagdata levert geen exacte intraday volgorde.
- Positiegrootte respecteert cash, 10% kasreserve en 5% modelrisico inclusief
  instap-/uitstapkosten en slippage bij stop. Een gap kan meer verlies geven.
- Kosten worden over werkelijke transactiebedragen geboekt, met volledige
  precisie. Open posities worden op de laatste slotkoers gewaardeerd zonder
  fictieve exitkosten. Geen belastingmodel.
- Statistieken zijn per ticker. Drawdown omvat de beginwaarde, Sharpe neemt de
  eerste dag tegenover startkapitaal mee, CAGR verschijnt alleen vanaf één jaar.
  Zonder verliestrades is profit factor onbekend in plaats van een kunstmatig
  enorm getal. Kosten zijn modelaannames in de betreffende koerseenheden en zijn
  economisch niet gelijkwaardig tussen valuta.

CSV's: backtest_summary_per_ticker.csv, backtest_trades.csv,
backtest_portfolio.csv en backtest_strategy_per_ticker.csv. De portfolio-CSV heeft
nu een tickerkolom en bevat afzonderlijke accounts. Er is geen performanceclaim
gevalideerd. De actuele tickerlijsten bevatten survivorship bias, aangepaste
bronkoersen zijn niet point-in-time gereconstrueerd en ontbrekende koersdagen
worden niet aangevuld. Slippage en transactiekosten zijn geen uitvoeringsgarantie.

## Bestanden en hertest

De live workflow bewaart `xxxV2_scan.json` en `xxxV2_signalen_DATUM.csv` als
Actions-artifact **xxxV2-signals**, zeven dagen. Complete in JSON heeft betrekking
op scan/opslag, niet op aflevering van de e-mail. Geen pushes naar main en geen
opslag of verwerking van handmatige transacties vanuit de workflow.

46 offline regressietests: strategieregels, geen toekomstige openingsfeature,
OHLCV/tijdzones, foutieve stops, volgende-openinguitvoering, dagelijkse exits,
sessiehoudduur, kosten/kas/risicogrens, open waardering, statistieken, read-only
live scan, rapportage, database/e-mail, gedeeltelijke verkoop, valuta, batchfouten,
atomische state en bescherming tegen heruitvoering na onderbreking.

`python -m unittest discover -s tests -p 'test_00xxxV2.py'`

Geen externe meldingen, database-inserts of brokerorders tijdens de tests.
Live hertest staat open via **run_00ultxxxV2.yml → Run workflow → main**.
Controleer dekking, aantallen strategie-signalen, opslagbevestiging, e-mail en
artifact xxxV2-signals. Deze hertest voert geen handmatige transacties of backtest uit.
