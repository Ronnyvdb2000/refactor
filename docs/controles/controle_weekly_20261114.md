# Controle weekly bots — 14 november 2026

## Structuur en rapportage

Zes scripts verplaatst naar weekly/; vijf workflows starten via python -m weekly.<module>. De bestandsnamen van de YAML-workflows zijn behouden. weekly/shared.py bundelt weekgrenzen, downloads en rapportage. Tickerlijsten en laatste_toppers*.json blijven in de repository-root.

Gebruikersinstructie: Telegram behouden voor de weekly bots en de volgende botcontroles. De vijf uitvoerworkflows geven Telegram-secrets door; ook de winnaarsanalyse levert een Telegram-rapport. E-mail blijft beschikbaar en de a/x-rapporten krijgen tekst-artifacts. Er zijn tijdens deze controle geen echte notificaties verstuurd.

## Herstelde logica

- Weekprestatie omvat nu de beweging vanaf de laatste slotkoers vóór maandag. De oude vijf slotkoersen leverden vier intervallen op. Vaste, voltooide kalenderweken voorkomen gedeeltelijke of verschoven weken bij handmatige runs.
- Downloads in batches van 100 en hergebruik per ticker binnen een proces; begrensde requests en aantallen bruikbare tickers per lijst. Positieve stijgers en negatieve dalers apart; grootste daler rang 1. Ontbrekende endpoints worden overgeslagen.
- JSON bevat data_version=2, weekgrenzen en werkelijke koersdatums. Backtest weigert legacy/verouderde lijsten en volgt op een succesvol rapport via workflow_run. Een leeg nieuw resultaat vervangt de oude winnaarslijst.
- JSON-workflows delen een concurrency-groep, bewaren alleen na succes op main en gebruiken rebase/gewone push zonder conflictvoorkeur -X ours.
- Eerste zoekdag telt nu mee bij piekdetectie. Downloads stoppen op de opgeslagen weekgrens; indicatoren vóór de piek. Momentum telt 63/252 intervallen; RVOL gebruikt eerdere volumes, de breakout vergelijkt met het recente gemiddelde en 52-weekgrenzen gebruiken High/Low. Geen actuele short/float als historische kenmerken. Hergebruik bij dezelfde ticker in meerdere lijsten.
- Opvolg berekent technische parameters alleen voor de bedoelde week. Geen actuele fundamentals terugdateren en geen Debt/Equity onder Debt/EBITDA opslaan. Ranks zijn beursgebonden, maximaal 30 dagen oud en moeten op de peildatum al opgeslagen zijn geweest; ophaling in groepen per beurs/datum.
- Databasefouten worden doorgegeven. Eerste ticker/beurs/week-snapshot blijft behouden bij herhaalruns, ook als het type verandert; parameter-upserts vullen alleen NULL aan. Advisory locks voorkomen extra type-rijen bij gelijktijdige nieuwe uitvoeringen. Geen bestaande databasegegevens gewijzigd of verwijderd tijdens deze controle.
- Correlaties zijn beschrijvend. Oudere gegevens blijven bewaard maar zijn uitgesloten vanaf de nieuwe analysegrens 2026-11-16. Geen actuele fundamentals in historische correlaties. Target volgt de werkelijk opgeslagen eerste en laatste dagslotkoers. Dubbels/conflicterende labels uitgesloten, minimum twee verschillende waarnemingsdagen en eindige/niet-constante waarden vereist. FDR corrigeert geen selectievertekening of afhankelijke ticker-weken.

## Verificatie

602 tests geslaagd, 9 lokale sockettests overgeslagen, 17 subtests geslaagd. Daarbinnen 26 nieuwe weekly-tests; deze zijn na de laatste backtestcorrecties opnieuw geslaagd. RUFF-controle op ontbrekende namen/ongebruikte imports en git diff --check geslaagd. Alle module-imports en backtest --help gecontroleerd.

Een echte lokale PostgreSQL-backend zonder sockets controleerde de gegenereerde SQL: tweede run behoudt de eerste topper, een lege nieuwe parameter overschrijft geen bestaande waarde en ranks van later, een verkeerde beurs of later ingeladen historie worden uitgesloten. De nieuwe as-of-query is ook read-only op Supabase uitgevoerd; de aanwezige kolommen en unieke sleutels zijn gecontroleerd.

Een kleine read-only Yahoo-controle voor AAPL, MSFT en ASML.AS leverde drie rendementsregels op met basis 2026-11-06 en einde 2026-11-13. Eén batchrequest voor AAPL had een lokale cache-lock; de bestaande afzonderlijke fallback herstelde dit. Geen volledige scan, workflowdispatch of echte Telegram/e-mail uitgevoerd. De eerstvolgende geplande run moet de nieuwe gedateerde JSON-uitvoer opleveren voordat de winnaarsanalyse de oude lijst kan vervangen.
