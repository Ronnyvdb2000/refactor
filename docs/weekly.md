# Weekly bots

Alle zes weekly scripts staan in `weekly/`. Start vanuit de repository-root als module; tickerbestanden en JSON-uitvoer blijven in de hoofdmap. `weekly/shared.py` bevat alleen weekgrenzen, rapportage en koerscache voor deze groep.

| Script | Workflow | Functie |
| --- | --- | --- |
| `weekly/weekly_report.py` | `weekly_run.yml` | Rapport voor 01–09 en 041a–059a; `laatste_toppers.json` |
| `weekly/weekly_report_x.py` | `weekly_run_x.yml` | Rapport voor 01–09 en 041x–059x; `laatste_toppers_x.json` |
| `weekly/weekly_report_opvolg.py` | `weekly_run_opvolg.yml` | Weekselecties en historische dagparameters naar Supabase |
| `weekly/weekly_db_opvolg.py` | Via opvolg en correlaties | Opslag en ranks op peildatum; geen zelfstandige workflow |
| `weekly/weekly_rep_backtest.py` | `weekly_runV2.yml` | Retrospectieve technische verkenning van weekwinnaars |
| `weekly/analyseer_weekly_correlaties.py` | `analyse_correlaties_weeklytop.yml` | Handmatige beschrijvende correlaties; CSV en e-mail |

Voorbeeld: `python -m weekly.weekly_report`. Dit start de echte scan met notificaties. Alleen `python -m weekly.weekly_rep_backtest --help` toont hulp zonder uitvoering.

## Weekgrenzen en rendement

De a-rapportage en opvolg starten vrijdag 23:00 UTC; de x-rapportage om 23:15 UTC. In België is dat zaterdag 00:00 in de winter en 01:00 in de zomer. Een handmatige run gebruikt de laatste volledige week; vóór vrijdag 23:00 UTC geldt nog de vorige week.

Weekprestatie loopt van de laatste beschikbare slotkoers vóór maandag tot het laatste beschikbare slot van de week, met aangepaste koersen. Vijf slotkoersen van maandag tot vrijdag zouden alleen vier dagrendementen meten. De basis mag maximaal zeven kalenderdagen vóór maandag liggen; de eindkoers maximaal drie dagen vóór vrijdag. Ontbrekende of oudere koersen worden uitgesloten en aantallen verschijnen per lijst. Positieve stijgers en negatieve dalers worden apart gerangschikt; de grootste daler krijgt rang 1. De neutrale steekproef blijft per lijst/week reproduceerbaar.

Elke unieke ticker wordt per proces eenmaal opgehaald in batches van maximaal 100, met begrensde requests. Verschillende runners hebben een eigen cache en kunnen samen nog steeds providerlimieten raken.

## Telegram, e-mail en workflow

Telegram blijft behouden voor de weekly rapporten, opvolg, winnaarsanalyse en correlatie-analyse. Secrets blijven in de YAML staan. Rapportberichten gebruiken tekst zonder Markdown-parsing; lange berichten worden opgedeeld. Verzendfouten worden gelogd. De a/x-rapporten leveren daarnaast tekst-artifacts en e-mail wanneer de e-mailsecrets beschikbaar zijn. De bestaande e-mail van correlaties blijft behouden. Bij volgende botcontroles wordt Telegram behouden volgens de gebruikersinstructie van 14 november 2026.

Beide JSON-schrijvende workflows delen een concurrency-groep en bewaren uitvoer alleen na succes op main. Git gebruikt rebase en gewone pushes; een conflict wordt zichtbaar in plaats van met `-X ours` overschreven. De backtest volgt via `workflow_run` op een geslaagd Weekly Report en blijft handmatig startbaar. De output van de oude JSON-lijsten zonder weekdatum wordt geweigerd; draai eerst de nieuwe Weekly Report. Iedere nieuwe topper bevat weekgrenzen, werkelijke koersdatums en `data_version=2`.

## Historische betrouwbaarheid

De opvolg bewaart technische parameters alleen op handelsdagen binnen de gekozen week. Ranks komen van dezelfde beurs, zijn maximaal 30 dagen oud en moeten op de peildatum al opgeslagen zijn geweest. Ontbrekende ranks blijven NULL. Actuele Yahoo-fundamentals en actuele short/float-data worden niet naar historische dagen gekopieerd. Ook Debt/Equity wordt niet meer onder de naam Debt/EBITDA opgeslagen.

Een herhaalrun behoudt de eerste selectie per ticker/beurs/week; een ander type veroorzaakt geen extra rij. Parameter-upserts vullen alleen NULL aan. Databasefouten worden doorgegeven, zodat een mislukte export geen groen succesrapport oplevert. Bestaande gegevens zijn tijdens deze refactor niet aangepast of verwijderd.

De correlatie-analyse gebruikt alleen weken vanaf 16 november 2026. Oudere waarden met de eerdere definities blijven bewaard en zijn uitgesloten. De analysetarget is het rendement tussen de eerste en laatste werkelijk opgeslagen dagslotkoersen binnen de week, dus niet het gehele vorige-vrijdag-tot-vrijdag rapport-rendement. Dubbels worden in de analyse uitgesloten en actuele fundamentals worden niet als historische verklarende variabelen getest. De steekproef is achteraf geselecteerd en ticker-weken zijn afhankelijk: p-waarden/FDR blijven verkennend en bewijzen geen voorspelkracht.

De zogenoemde backtest blijft een retrospectieve winnaarsanalyse. De piekdag wordt achteraf gekozen; dit is geen onafhankelijke rendementstest van een verhandelbare strategie. Koersdata stopt bij de opgeslagen weekgrens; indicatoren stoppen vóór de gevonden piekdag. De eerste dag van het zoekvenster telt nu mee. Short/float blijven historisch onbekend.
