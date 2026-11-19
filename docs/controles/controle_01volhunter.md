# Controle Volatiele Dip-Scan — 14 november 2026

Uitsluitend Ronnyvdb2000/refactor, los van refactor-lab.
Script bot_01volhunter.py; workflow run_01volatieledipscan.yml.

## Hersteld

- Telegram hersteld op 19 november; e-mail en database behouden. Ook nul kandidaten geeft een
  rapport. Opslagbevestigingen worden geteld; opslag-, mail- en analysefouten,
  een geheel onbruikbare scan of uitgeput aanvraagbudget geven een foutstatus.
- Alle bestaande a-lijsten 041–059 blijven ongewijzigd. Unieke tickers worden
  eenmaal opgehaald/geanalyseerd; overlap wordt per beurs gerapporteerd.
  Aantallen geanalyseerd, binnen ATR-band, kandidaten en topselecties zijn apart.
- OHLCV en datum worden gevalideerd: eindige positieve koersen, coherente dagrange,
  niet-negatief volume en positief laatste volume. Geen actuele/onvoltooide dag,
  koers ouder dan zeven dagen of indicator over een ongeldige rij/lang gat.
  Minimaal honderd opeenvolgende bruikbare sessies vereist. Verwijderde rijen
  en databeperkingen zijn zichtbaar. Dubbele datums houden de laatste rij.
- Beide Yahoo MultiIndex-kolomvolgordes ondersteund. Geheel lege batchkalenderrijen
  worden verwijderd; geobserveerde ongeldige laatste rijen blijven zichtbaar.
  Ontbrekende batchdata krijgt een individuele heraanvraag. Een ongeldige laatste
  OHLCV krijgt Yahoo-repair; alleen herstel van dezelfde laatste afgeronde dag
  wordt aanvaard en expliciet gemarkeerd als niet onafhankelijk geverifieerd.
- Seriële batches van standaard vijftig, dertig seconden rust tussen batches en
  een halve seconde na individuele aanvragen/herstel. Expliciete Yahoo-rate-limit
  krijgt maximaal drie herpogingen na 60/180/300 seconden; daarna stoppen verdere
  aanvragen en worden reeds bruikbare resultaten als gedeeltelijke scan gemeld.
- Aanvraagbudget 225 minuten bij workflowtimeout 240 minuten. Controle tussen
  aanvragen/pauzes, niet tijdens een lopende provideroproep: geen harde garantie
  dat een trage batch binnen de marge klaar is. Timeout vijftien seconden per
  aanvraag. yfinance kan batchfouten zelf afvangen; individuele fallback probeert
  ontbrekende histories opnieuw. Dit garandeert geen volledige Yahoo-dekking.
- Volledige prijs-/indicatorprecisie tot presentatie, stabiele ranking bij gelijke
  score en werkelijke koersdag in rapport/database. Bestaande parameter
  pct_from_high behouden naast expliciet benoemde pct_from_20d_high.
- Workflow: scikit-learn toegevoegd voor Yahoo-repair (DBSCAN-import gecontroleerd),
  ongebufferde logs, concurrency en read-permissies. Vrijdag 23:10 UTC,
  Python 3.11 en de bestaande strategiegrenzen blijven behouden.

## Strategie en grenzen

ATR14 tussen 6 en 60 procent, IBS maximaal 0,25, RSI3 maximaal 15, standaard
slotkoers boven MA100. Score ATR-procent maal (1 - IBS), top drie per beurs.
Referentiestop slotkoers min 1,5 ATR, doel slotkoers plus 2,5 ATR; ongeldige
referentieniveaus leveren geen selectie. Geen wijziging van deze instapregels.

Dit is een eigen dagelijkse koersheuristiek, geen gevalideerde rendementsclaim.
Stop/doel zijn referenties, geen uitvoeringsprijzen, orders of exit-tracking.
Geen portefeuille, sizing, kostenmodel of backtest toegevoegd. Koersen behouden
bronvaluta/subeenheid; geen verzonnen EUR-label of FX-conversie. Yahoo-repair
is providerherstel; geen externe prijsverificatie of beurskalender. Een volledig
ontbrekende recente sessie kan niet onafhankelijk uit een batch worden bewezen.
Databeperkingen geven een ONVOLLEDIG-rapport; verwachte provider-/historiebeperkingen
maken de job niet op zichzelf rood zolang er bruikbare analyses zijn.

## Validatie en vervolg

36 offline tests onder lokale Python 3.12 geslaagd: indicatoren op synthetische
OHLCV, precisie/kleine prijzen, filters, ATR-teller, ongeldige koersen/volume,
datums/versheid/gaten, kolomvolgordes, batchfallback, herstel van laatste dag,
begrensde rate limiting/deadline, cache/ranking, database/e-mail en workflow.
Yahoo, database en verzending zijn gemockt. Geen echte aanvragen, inserts of
notificaties uitgevoerd. Compile en git diff --check slagen.

```
python -m unittest discover -s tests -p 'test_01volhunter.py' -q
python -m py_compile bot_01volhunter.py
```

Live hertest blijft open: start run_01volatieledipscan.yml handmatig op main.
Controleer volledige a-lijstdekking, laatste koersdagen, repairmeldingen,
rate limits, databasebevestigingen en ontvangen e-mail en Telegram.

## Vervolgcontrole — 19 november 2026

De workflow geeft opnieuw `TELEGRAM_TOKEN` en `TELEGRAM_CHAT_ID` door. Het volledige
rapport, inclusief lege resultaten en databeperkingen, wordt zonder opmaak verstuurd.
Lange rapporten worden opgesplitst in delen van maximaal 3900 UTF-16-eenheden,
ook bij emoji. Een mislukt deel verhindert het proberen van latere delen of e-mail
niet; de job meldt daarna een fout. De tests vervangen verzending door mocks.

De actuele tussenpauze is vijf seconden, zoals beschreven in [scantempo](../scantempo.md).
De download blijft serieel (`threads=False`), met een aanvraagtimeout van vijftien
seconden en een scanbudget van 225 minuten. De eigen downloader gebruikt nog
individuele fallback/repair en maximaal drie rate-limit-herpogingen; de gedeelde
downloaderbegrenzing geldt hier niet automatisch. Volledige live doorlooptijd en
dekking zijn hiermee nog niet afgetekend. Er is geen nieuwe scan gestart.
