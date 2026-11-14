# Controle VCP — 13 november 2026

## Patroon en live scan

De oorspronkelijke achtpuntscore en minimumscore 4/8 blijven behouden. Het is een
eigen VCP-heuristiek, geen garantie van een uitbraak en geen officiële implementatie
van een gepubliceerde strategie. De bestaande trendvoorwaarde blijft
Close > MA50 > MA150 > MA200. Er is geen extra trendfilter verplicht gemaakt.

De eerste correctie vanaf de hoogste bevestigde piek wordt nu meegenomen.
Opeenvolgende pieken kunnen niet dezelfde daling dubbel als contractie tellen.
Contracties zijn chronologisch en overlappen niet. Van maximaal vijf worden de
recentste gebruikt. De pivot is de hoogste top van de gebruikte contracties.
Swingpunten vereisen vijf koersdagen bevestiging rechts; bij historische scans
gebeurt dit uitsluitend met op dat moment beschikbare data. De indexen van de
contracties verwijzen naar het 120-daagse detectievenster.

Een volume-uitbraak wordt vergeleken met de 50 voorgaande sessies. De signaaldag
verhoogt zijn eigen gemiddelde niet meer. Percentages, volumes, koersen, ATR en
stops worden pas afgerond in de presentatie. Ontbrekende ATR wordt niet door
een fictieve 2% vervangen. Een stop moet positief en lager dan de koers zijn.

De scan valideert OHLCV, tijdzones, datumvolgorde, dubbele datums, eindige positieve
prijzen en niet-negatief volume. Alleen koersdagen vóór vandaag tellen mee;
320 geldige koersdagen zijn vereist. De laatste koers mag maximaal zeven
kalenderdagen oud zijn en recent volume mag niet geheel nul zijn. Dit is een
eenvoudige datumcontrole, geen specifieke beurskalender. Verwijderde slechte
rijen kunnen gaten achterlaten; de brongegevens worden niet onafhankelijk
geverifieerd. De gemeenschappelijke downloader behoudt zijn herpoging per ticker.

Elke unieke ticker wordt eenmaal geanalyseerd. Een kandidaat kan in meerdere
beurslijsten voorkomen en wordt dan per beurs gerapporteerd en opgeslagen.
Rapporten bevatten ontbrekende data, lege lijsten en analysefouten. Ook nul
kandidaten levert een e-mail en lege uitvoer met vaste kolommen op. Mislukte
analyses, geheel onbruikbare data, database-opslag en e-mailverzending laten de
run mislukken; gedeeltelijke datadekking geeft [ONVOLLEDIG].

Koersen blijven in oorspronkelijke koerseenheden. De fictieve europortefeuille,
aantallen aandelen en R/R met de pivot als koersdoel zijn uit de live rapportage
verwijderd. De referentie-entry is een modelschatting met slippage, geen order.
CSV en database bevatten ook de datum van de gebruikte koerssessie.

## Backtest

De vorige backtest controleerde alleen maandagen, stapte op de signaalslotkoers
in, telde scanmomenten als dagen en berekende instapkosten inconsistent. Daarnaast
werden bedragen uit verschillende valuta als één europortefeuille opgeteld.
De vervangende backtest is expliciet een afzonderlijke simulatie per ticker;
hij pretendeert geen gezamenlijk portefeuille- of EUR-rendement.

- Elke ticker begint met 50.000 oorspronkelijke koerseenheden. De kostenfunctie
  uit common wordt als vaste modelaanname in diezelfde eenheden toegepast.
  Hierdoor zijn de vaste kosten niet economisch gelijkwaardig tussen valuta.
- De ingestelde datums worden gerespecteerd, met twee kalenderjaren aanloopdata.
  De runner vereist 320 geldige sessies vóór de start; onvoldoende historie
  wordt gemeld. Nieuwe noteringen worden daarmee uitgesloten van deze simulatie.
- Maandag na het slot ontstaat het signaal. Instap volgt op de eerstvolgende
  beschikbare opening, maximaal zeven dagen later. Geen maandagdata betekent
  geen scan op die week. Een opening boven het modeldoel of onder de stop geeft
  geen instap.
- Stop en modeldoel worden elke beschikbare sessie gecontroleerd. Gaps worden
  op de opening verwerkt; raakt één dag zowel stop als doel, dan gaat de stop
  conservatief voor. Ontbrekende koersdagen worden niet gereconstrueerd.
- Het bestaande modeldoel van 1,20 × pivot blijft uitsluitend in de backtest.
  De maximale houdduur is 60 afgeronde handelssessies, daarna uitstap op de
  volgende beschikbare opening. De trade-log telt ook de uitstapsessie mee.
- Instap en uitstap gebruiken slippage en kosten over het echte notional.
  Het aantal aandelen past binnen cash en het modelverlies bij stop inclusief
  kosten blijft binnen 5% van de accountwaarde. Gaps kunnen meer verlies geven.
- Open posities worden gewaardeerd op de laatste beschikbare slotkoers zonder
  fictieve verkoopkosten. Die koersdatum staat in de samenvatting. Geen
  belastingmodel: de eerdere arbitraire inhouding van 10% is verwijderd.

Uitvoer: vcp_backtest_per_ticker.csv en vcp_backtest_trades.csv. Er zijn geen
statistische prestatieclaims of live backtestresultaten gevalideerd. De huidige
tickerlijsten veroorzaken survivorship bias; historische delistings ontbreken.
Koersen zijn door de leverancier aangepast en niet point-in-time gereconstrueerd.

## Workflow en uitvoer

Telegram blijft uit. De bestaande TradingAgents-job blijft uitgeschakeld.
Ongebruikte Turso-variabelen en libsql-installatie zijn uit deze workflow gehaald.
De bot maakt vcp_signalen_DATUM.csv nu daadwerkelijk aan. vcp_signals.json behoudt
het veld tickers voor de bridge en voegt dekking en opslagstatus toe. Het veld
complete beschrijft scan/opslag, niet de aflevering van de e-mail.

JSON en CSV worden als artifact vcp-signals zeven dagen bewaard, ook bij een
mislukte scan als bestanden beschikbaar zijn. De eerdere git-pushstap is verwijderd:
resultaten zijn via Actions downloadbaar en er worden geen concurrerende merges
of pushes naar main meer vanuit deze workflow gedaan. De workflow krijgt daarom
alleen contents: read. Dat verandert de opslaglocatie van de bedoelde CSV-historie.

## Verificatie

30 offline regressietests: begincontractie, overlap, krimp, volumereferentie,
precisie, ongeldige data en stops, OHLCV/tijdzones, volgende-openinginstap,
dagelijkse stops, gaps, intraday volgorde, sessiehoudduur, kostenaansluiting,
cash-/risicogrens, open waardering, datumbereik, verouderde signalen en
live uitvoer/verzending/opslag. Geen externe berichten of databasewijzigingen.

`python -m unittest discover -s tests -p 'test_00vcp.py'`

Live hertest nog te doen via run_00vcp.yml op main. Beoordeel datadekking,
contracties/selecties, databasebevestiging, e-mail en artifact vcp-signals.
