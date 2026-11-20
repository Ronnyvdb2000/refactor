# Opschoning rapportuitvoer — 20 november 2026

Basis: `894ae984ea3a1c5bca28a7472ecdaf78745a15fd` op main. Vervolg op de opschoning van handmatige controles.

## Omgezette uitvoer

| Producent | Nieuwe rapporten | Afnemer |
| --- | --- | --- |
| `analyses.analyse_parameter_ic` | `results/parameter_ic_matrix.json` | Workflowcontrole en artifactpad aangepast naar JSON |
| `meta_models.vergelijk_meta_par_10d` | `folds.json`, `top_selecties.json` | Bestaande results-directory als artifact |
| `meta_models.vergelijk_meta_par_horizons` | `folds.json`, `top_selecties.json` | Directory-artifact; Telegram leest dezelfde `samenvatting.json` |
| `meta_models.vergelijk_meta_par_horizons_diagnose` | Bovenstaande plus `diagnose_<categorie>.json` | Directory-artifact; Telegram-samenvatting behouden |
| `xLightgbmV2.py` | `lightgbmV2_<h>_correlations.json`, `_feature_importance.json`, `_test_predictions.json` | Bestaand results-directory-artifact |

Deze vijf producenten hebben geen CSV-detailuitvoer meer. Repositoryzoekwerk vond geen interne lezers van de vervangen detailbestanden; de IC-workflow had wel een vast CSV-artifactpad en is mee aangepast. Eventuele externe gebruikers van de oude CSV-bestanden moeten naar records-JSON overschakelen.

De gedeelde `report_utils.write_json` bewaart records, kolomnamen, ISO-datums en Unicode. Ontbrekende waarden worden `null`. Lege vergelijkingsproeven overschrijven de nieuwe JSON-detailbestanden met lege lijsten. De oude CSV-bestanden van eerdere, externe runs worden niet verwijderd.

`common.exports` behoudt de bestaande CSV-interface via dezelfde lichte rapportmodule. De optionele CSV-schakelaar blijft standaard uit. `a_par_model` gebruikt die interface nu zonder de providerpackage te importeren; dit voorkomt dat de 10d-vergelijkingsworkflow alleen voor rapportage yfinance moet installeren.

## Rapportfout hersteld

De IC-console drukte de 10d-IC af onder het label hit rate, ook bij andere horizons. Het rapport en de console bevatten nu de werkelijk berekende `hit` per horizon. De IC-/IR-berekening is behouden. Een dataset zonder numerieke factoren levert een lege JSON-lijst op in plaats van een sorteerfout. Importeren maakt geen results-map meer aan.

## Controle

64 gerichte tests geslaagd: JSON-records, ontbrekende waarden, lege runs, datums, rapportvolgorde, horizonlabels, bestaande CSV-opt-in, forward-horizoncontroles, workflowplanning en LightGBM-preflight. De tests gebruiken synthetische gegevens en mocks; geen live modeltraining, databasewrites of Telegram-berichten uitgevoerd. Audit van 66 workflows: geen fouten of waarschuwingen. YAML en shell-syntax van de aangepaste IC-workflow gecontroleerd.

Modelbundels (`.pkl`), trainings- en selectielogica, Telegram-samenvattingen, Supabase-tabellen en de dagelijkse onderzoekssimulatie zijn niet veranderd.

## Nog over

Directe CSV-schrijfpunten bestaan nog in provider-/forward-returndiagnostiek, expliciete CLI-exportopties en weekly-checkpoints/dekkingsrapporten. Die hebben verschillende afnemers. Vooral de checkpointbestanden zijn ook invoer voor hervatten en mogen pas samen met hun lezers worden omgezet.
