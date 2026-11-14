# Scantempo — 14 november 2026

Aanpassingen in `Ronnyvdb2000/refactor` voor de dipscan en de vijf daarvoor laatst aangepaste bots. Bestaande tickerlijsten, selectieregels en rapportagekanalen blijven behouden. Er is geen verkleining van het tickeruniversum.

| Bot | Aanpassing |
| --- | --- |
| Volatiele Dip-Scan | Pauze tussen batches van 50 tickers van 30 naar 5 seconden. Instelbaar via `BATCH_PAUSE_SEC` (0–30). |
| BeursSignalen | Full-scanpauze per 100 analyses van 30 naar 5 seconden; vaste wachttijd per nieuwe analyse van 0,5 naar 0,1 seconde. Live-modus blijft 0,15 seconde. |
| Hoogland | Basispauze per ticker naar 0,05 seconde; extra vertraging na rate limits begrensd op 0,5 seconde en teruggezet na een aanvraag zonder rate limit. |
| Kasstroom | Vaste pauze per unieke ticker van 0,15 naar 0,05 seconde. |
| Marktsentiment | Downloads in batches van 100 met voortgangsmeldingen en request-time-out van 15 seconden. Data eenmaal per ticker groeperen in plaats van de volledige tabel telkens te filteren. |
| Seizoenseffecten | Request-time-out van 15 seconden, voortgang per downloadbatch en de tickerlijst eenmaal sorteren. Deze bot had geen lange vaste pauze. |

Bij 13.945 unieke tickers heeft de dipscan 279 batches: 278 tussenpauzes kosten nu circa 23 minuten in plaats van 139 minuten. Downloads, herstelverzoeken, providerlimieten en analyse komen daar nog bij. Een volledige scan binnen het bestaande tijdsbudget is daarmee niet gegarandeerd.

De herstelpauzes bij echte Yahoo-limieten blijven behouden. De optionele `request_timeout` in `common.io.download_history` wordt voor zowel de batchaanvraag als individuele fallbackaanvragen doorgegeven. De standaardaanroepen van andere bots veranderen niet. Deze time-out begrenst een afzonderlijk providerverzoek, niet de totale batchduur.

Voortgangsmeldingen zijn ook toegevoegd aan BeursSignalen, Hoogland en Kasstroom. Gelijktijdige workflows delen geen lokale cache of Yahoo-aanvraagbudget; deze wijziging introduceert geen gezamenlijke wachtrij.

Wijzigingen gelden bij een volgende workflowrun. Reeds gestarte runs gebruiken hun bestaande checkout.

## Verificatie

Gerichte regressietests voor de zes bots, gedeelde IO en `tests/test_scan_pacing.py`. Alle externe aanvragen, notificaties, databasewrites en wachttijden worden in de nieuwe scantempotests vervangen door mocks. De tests controleren onder meer volledige batchdekking, het behouden van herstelpauzes, terugzetten van de Hoogland-wachttijd en het doorgeven van time-outs aan fallbackaanvragen.

Resultaat: 237 tests en 11 deeltests geslaagd. Geen live scans gestart.
