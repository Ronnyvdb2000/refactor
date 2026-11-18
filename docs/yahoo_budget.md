# Gezamenlijke Yahoo-belasting — 15 november 2026

Scope: `Ronnyvdb2000/refactor`, niet-weekly workflows. Er zijn 38 Yahoo-jobs in 35 workflows aan vier gedeelde GitHub Actions-jobgroepen toegewezen. De exacte toewijzing staat in [maintenance/yahoo_lanes.py](../maintenance/yahoo_lanes.py) en het gegenereerde [workflowoverzicht](workflow_planning.md).

## Vier afzonderlijke wachtrijen

| Groep | Functie | Jobs |
| --- | --- | --- |
| `refactor-yahoo-daily` | Dagelijkse basisbots en de uitgebreide combiner | 21 vaste jobs; cointegratie in live-modus |
| `refactor-yahoo-full` | Hoogland/BeursSignalen full, dipscan, tickerfilter, technische backfill en oudere XGBoost train/backtest | 7 vaste jobs; cointegratie in backtest-modus |
| `refactor-yahoo-features` | Technicals, fundamentals, forward returns, koersdiagnose en twee koersafhankelijke analyses | 6 |
| `refactor-yahoo-timed` | MR ORB en de EU/NA-superbots | 3 |

Elke groep laat maximaal één toegewezen job tegelijk uitvoeren, over verschillende workflows en branches in dezelfde repository. Alle groepen gebruiken `queue: max` en `cancel-in-progress: false`. Daarmee kunnen maximaal vier toegewezen Yahoo-jobs tegelijk actief zijn. Er is geen aparte Yahoo-groep per branch die deze begrenzing kan omzeilen. GitHub groepeert concurrency per repository; `refactor-lab` is niet gewijzigd en valt hier niet onder.

Een lange full-scan bezet geen dagelijkse, feature- of tijdgebonden plaats. Ook de twee jobs van een handmatige EU/NA-superbotrun delen dezelfde tijdgebonden plaats. De bestaande guards tegen dubbele runs blijven op workflowniveau behouden. Bij Triplex is de vroegere jobguard naar workflowniveau verplaatst, zodat de nieuwe gedeelde jobguard daarnaast kan werken. Workflowguard en jobguard gebruiken nooit dezelfde groep, om vastlopen door geneste locks te voorkomen.

Cointegratie kiest voor een handmatige `mode=backtest` de full-groep; ontbrekende modus/cron of `live` gebruikt daily. De oude XGBoost-workflow heeft afzonderlijke live-, train- en backtestjobs, met overeenkomstige groepen. De reeds gedeactiveerde experimentele workflows en VCP-bridge blijven gedeactiveerd; de budgettoewijzing activeert ze niet.

De standaard `queue: max`-grens is volgens [GitHub](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) honderd wachtende jobs/runs per groep. Werkelijke startvolgorde hangt af van het moment waarop jobs hun groep bereiken. Deze wachtrijen zijn dus geen feature-/labelafhankelijkheidsketen.

## Waarom afzonderlijke groepen?

De bekeken succesvolle Actions-runs geven sterk verschillende looptijden (duur van de gehele run, inclusief opstart/afhandeling, geen zuivere requesttijd):

| Voorbeeld | Waargenomen duur |
| --- | --- |
| Combi-volatiel — run 43683196583 | circa 5 minuten |
| Hoogland live — run 43591237610 | circa 7 minuten |
| Kasstroom — run 43567966737 | circa 11 minuten |
| Technicals — run 43658426395 | circa 12 minuten |
| Forward returns, eerste 60d-aanvulling — run 43685780913 | circa 32 minuten |
| Hoogland full — run 43562147893 | circa 171 minuten |
| BeursSignalen full — run 43567609721 | circa 227 minuten |
| Dipscan — run 43594937137 | circa 210 minuten |

Deze runs stammen niet allemaal uit dezelfde codeversie of identieke provideromstandigheden. Ze voorspellen geen nieuwe vaste eindtijd. De verschillen maken één gezamenlijke wachtrij voor alle scans wel ongeschikt: een dagelijkse scan zou achter urenlange full-scans kunnen blijven staan.

## Binnen een download

`common.io.download_history` gebruikt standaard maximaal **vier yfinance-workers**, in plaats van het eerdere `threads=True` waarbij yfinance de paralleliteit koos. `YAHOO_DOWNLOAD_WORKERS` kan dit verlagen naar 1, 2 of 3; waarden buiten 1–4 worden vóór een providerverzoek geweigerd. De aparte tickerfilter-batchdownload gebruikt dezelfde workergrens. Individuele herstelrequests blijven serieel (`threads=False`). Er worden geen tickerlijsten ingekort en er wordt geen steekproef genomen.

Een gelogde of opgegooide Yahoo-rate-limit wordt in de gedeelde koersdownloader als `YahooRateLimited` doorgegeven. Een lege of gedeeltelijke 429-batch mag daardoor niet ontaarden in een individuele request per ontbrekende ticker. Treedt de limiet op tijdens herstel, dan stoppen de resterende herstelrequests van die aanroep. Niet aan throttling gerelateerde ontbrekende tickers, zoals een 404, behouden hun bestaande herstelpad. De aanroepende bot behoudt zijn fout-/herstel- en rapportagebeleid; dit is geen nieuwe globale afkoeltimer.

## Grenzen en live opvolging

- Dit begrenst **jobs en downloadworkers**, niet exact het aantal HTTP-verzoeken per seconde. Eén yfinance-actie kan meerdere HTTP-verzoeken uitvoeren. Vier groepen met vier workers vormen geen bewezen limiet van zestien totale HTTP-verzoeken.
- Weekly-workflows en hun eigen downloader zijn niet gewijzigd en staan buiten deze begroting. Hun belasting kan erbij komen. Afstemming met de andere controle blijft nodig.
- Training/scoring op opgeslagen data verbruikt geen Yahoo-plaats. Twelve Data en externe LLM-/nieuwsproviders zijn geen onderdeel van dit Yahoo-budget. Los gestarte lokale scripts of externe repositories delen de Actions-wachtrijen niet.
- Cron-tijden, selectieregels, tickeruniversum, notificatiekanalen en bestaande joblimieten blijven behouden. Een job kan wel later werkelijk starten doordat hij wacht. Dat is de bewuste afweging om minder gelijktijdige druk op Yahoo te zetten. Dit kan dagelijkse rapportage vertragen; het voorkomt geen externe bronblokkade.
- Reeds gestarte oude checkouts nemen niet achteraf een nieuwe plaats in. Er is tijdens deze wijziging geen scan gestart, gestopt of opnieuw uitgevoerd.
- Volgende live runs moeten de wachttijd, doorlooptijd, datadekking en het aantal 429-fouten bevestigen. Pas daarna is bewezen of vier plaatsen en vier workers in deze omgeving goed uitpakken. De afzonderlijke openstaande controles op feature-/labelvoltooiing vóór modelpublicatie blijven nodig.

## Herhaalbare controle

```bash
python -m maintenance.audit_workflows --format json
python -m maintenance.audit_workflows --format markdown > docs/workflow_planning.md
python -m pytest tests/test_yahoo_budget.py -q
```

De audit vergelijkt de daadwerkelijke jobgroepen met de centrale toewijzing en controleert ook de wachtplaatsen en de scheiding van workflow-/joblocks. Hij start geen job en benadert geen provider.

Verificatie: de volledige offline suite onder `tests/` heeft **696 tests en 17 deeltests geslaagd**, met 12 optionele tests overgeslagen. Nieuwe regressies dekken alle 205 synthetische tickers bij de workergrens, instelbare lagere paralleliteit, weigering van ongeldige configuratie, gelogde en opgegooide 429's, gedeeltelijke batches, afbreken van herstel, behoud van 404-herstel, herstel van loggerinstellingen, de aparte tickerfilterdownload, alle jobgroepen en de afzonderlijke live/backtest-route. Externe aanvragen en wachttijden zijn in deze tests vervangen door mocks. Native wachtrijgedrag is gebaseerd op GitHub's gedocumenteerde concurrencycontract; er is geen live dispatch gedaan om dit te demonstreren.
