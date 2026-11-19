# Basisstrategieën in strats

Alle 24 `bot_00*`- en `bot_01*`-scripts staan in deze Python-package. Start ze vanuit de repository-root als module:

```bash
python -m strats.bot_00kr live
python -m strats.bot_01hoogl full
python -m strats.bot_01volhunter live
```

Dit zijn echte scans met de bestaande opslag en notificaties. Niet iedere bot ondersteunt `--help`; gebruik dat niet als algemene importtest. Een module importeren start geen scan.

Python-imports gebruiken bijvoorbeeld `from strats import bot_01kasstr`. De oude root-imports en directe scriptcommando’s zijn vervangen; er zijn geen root-wrappers toegevoegd. Tickerlijsten, portefeuillestaten, rapporten en modelbestanden worden vanuit de repository-root gebruikt, zoals vóór de verplaatsing.

## Scripts en workflows

| Module | Workflow(s) |
| --- | --- |
| [bot_00Fisher](bot_00Fisher.py) | [run_00fisher.yml](../.github/workflows/run_00fisher.yml) |
| [bot_00cs](bot_00cs.py) | [run_00cs.yml](../.github/workflows/run_00cs.yml) |
| [bot_00db](bot_00db.py) | [run_00db.yml](../.github/workflows/run_00db.yml) |
| [bot_00dm](bot_00dm.py) | [run_00dm.yml](../.github/workflows/run_00dm.yml) |
| [bot_00graham](bot_00graham.py) | [run_00graham.yml](../.github/workflows/run_00graham.yml) |
| [bot_00greenblatt](bot_00greenblatt.py) | [run_00greenblatt.yml](../.github/workflows/run_00greenblatt.yml) |
| [bot_00kr](bot_00kr.py) | [run_00kr.yml](../.github/workflows/run_00kr.yml) |
| [bot_00mail](bot_00mail.py) | [run_00mail.yml](../.github/workflows/run_00mail.yml) |
| [bot_00mr](bot_00mr.py) | [run_00mrEOD.yml](../.github/workflows/run_00mrEOD.yml), [run_00mrORB.yml](../.github/workflows/run_00mrORB.yml) |
| [bot_00ms](bot_00ms.py) | [run_00ms.yml](../.github/workflows/run_00ms.yml) |
| [bot_00oshaughnessy](bot_00oshaughnessy.py) | [run_00oshaughnessy.yml](../.github/workflows/run_00oshaughnessy.yml) |
| [bot_00supervar](bot_00supervar.py) | [run_00supervar.yml](../.github/workflows/run_00supervar.yml) |
| [bot_00ultmail](bot_00ultmail.py) | [run_00ultmail.yml](../.github/workflows/run_00ultmail.yml) |
| [bot_00vcp](bot_00vcp.py) | [run_00vcp.yml](../.github/workflows/run_00vcp.yml) |
| [bot_00xxxV2](bot_00xxxV2.py) | [run_00ultxxxV2.yml](../.github/workflows/run_00ultxxxV2.yml) |
| [bot_01beurssign](bot_01beurssign.py) | [run_01beurssign.yml](../.github/workflows/run_01beurssign.yml), [run_01beurssignfull.yml](../.github/workflows/run_01beurssignfull.yml) |
| [bot_01cointegr](bot_01cointegr.py) | [run_01cointr.yml](../.github/workflows/run_01cointr.yml) |
| [bot_01hoogl](bot_01hoogl.py) | [run_01hoogl.yml](../.github/workflows/run_01hoogl.yml), [run_01hoogl_full.yml](../.github/workflows/run_01hoogl_full.yml) |
| [bot_01kasstr](bot_01kasstr.py) | [run_01kasstr.yml](../.github/workflows/run_01kasstr.yml) |
| [bot_01marktsent](bot_01marktsent.py) | [run_01marktsent.yml](../.github/workflows/run_01marktsent.yml) |
| [bot_01repititief](bot_01repititief.py) | [run_01repititief.yml](../.github/workflows/run_01repititief.yml) |
| [bot_01volhunter](bot_01volhunter.py) | [run_01volatieledipscan.yml](../.github/workflows/run_01volatieledipscan.yml) |
| [bot_01xgboost](bot_01xgboost.py) | [run_02xgboost.yml](../.github/workflows/run_02xgboost.yml) |
| [bot_01xgboostMeta](bot_01xgboostMeta.py) | [run_02xgboostMeta.yml](../.github/workflows/run_02xgboostMeta.yml) |

## Verplaatsing van 19 november 2026

27 workflowbestanden gebruiken nu `python -m strats.<bot>`. De bestaande argumenten, secrets, cron-tijden, rechten, concurrencygroepen, timeouts, artifacts en publicatiestappen zijn inhoudelijk gelijk gebleven. Uitgeschakelde workflows en de uitgeschakelde TradingAgents-job blijven uitgeschakeld. De strategie-identificaties in database en rapportage zijn niet hernoemd; Telegram blijft behouden. TradingAgents en refactor-lab blijven apart.

Verificatie: alle 24 strategiemodules veilig geïmporteerd; 784 tests en 19 deeltests geslaagd, dertien optionele tests overgeslagen, met uitgaand netwerkverkeer geblokkeerd. Workflowaudit: 65 workflows zonder fouten of waarschuwingen. Alle YAML-bestanden geladen en 208 shellstappen met `bash -n` gecontroleerd. Workflowconfiguratie is structureel met de vorige main vergeleken: alleen de Python-startcommando’s verschillen. Geen live scans gestart.
