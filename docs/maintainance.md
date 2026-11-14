# Maintainance — handmatige backfills

De twee backfills staan in `maintainance/` in `Ronnyvdb2000/refactor`. Start ze als Python-module vanuit de repository-root. De bestaande workflows blijven handmatig beschikbaar onder `.github/workflows/`.

| Script | Workflow | Standaard |
| --- | --- | --- |
| [Technische features](../maintainance/backfill_technische_features.py) | [Backfill technische features](../.github/workflows/backfill.yml) | `dry_run=true`: alleen controle/rapportage |
| [Rank-kolommen](../maintainance/backfill_rank_kolommen.py) | [Backfill rank-kolommen (eenmalig)](../.github/workflows/backfill_rank_kolommen.yml) | `apply=false`: alleen controle/rapportage |

## Starten in GitHub Actions

Kies de gewenste workflow, klik **Run workflow** en selecteer **main**. De namen van de workflows en hun invoervelden zijn behouden. Een eerste controle kan met de standaardinstellingen worden gestart. Alleen expliciet `dry_run=false` respectievelijk `apply=true` schakelt schrijven in.

De technische workflow bewaart zijn log nog steeds onder `results/backfill_log.txt` en uploadt hetzelfde Actions-artifact. Module-imports worden vooraf gecontroleerd. `pipefail` voorkomt dat `tee` een mislukte Python-uitvoering verbergt.

## Nieuwe commando's

```bash
python -m maintainance.backfill_technische_features
python -m maintainance.backfill_rank_kolommen
python -m maintainance.backfill_rank_kolommen --apply
```

De technische backfill behoudt de bestaande `BACKFILL_DRY_RUN`, `BACKFILL_BATCH_SIZE`, `BACKFILL_TICKER_FILTER` en `BACKFILL_SLEEP`-variabelen. `SUPABASE_DB_URL` wordt nog steeds via de workflow doorgegeven. Het script importeert de berekening uit `bouw_generieke_technicals.py` in de hoofdmap; de module-uitvoering zorgt dat die import blijft werken.

De oude losse scriptpaden in de hoofdmap zijn vervangen. Gebruik `--help` bij beide modules om de beschrijving te bekijken zonder een databaseverbinding te openen.

## Gebruik

Dit zijn onderhoudstaken, geen periodieke bots. De technische backfill kan nuttig zijn bij nieuwe indicatoren of ontbrekende historische features; onvoldoende koershistorie kan niet altijd worden hersteld. De rank-backfill is beschikbaar om opgeslagen JSON-waarden naar rank-kolommen over te brengen als daar opnieuw behoefte aan ontstaat.

Deze verplaatsing voert zelf geen backfill uit en verandert geen databasegegevens.

## Controle

De nieuwe modulecommando's en de shellcommando's uit beide YAML-bestanden zijn getest. Tests controleren de dry-run/apply-keuze, het behoud van de veilige technische dry-run-default en de foutcode van Python door de logpipeline. Databaseverbindingen en backfill-schrijfacties zijn hiervoor niet uitgevoerd.
