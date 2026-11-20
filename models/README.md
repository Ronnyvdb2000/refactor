# Models

Deze map bevat XGBoost- en LightGBM-training/scoring én de opgeslagen PKL- en JSON-modellen. Er is geen aparte artifacts-map. Voer de commando's uit vanuit de repository-root.

| Module | Functie |
| --- | --- |
| `models.xgboostV3` | Productietraining per horizon, met veilige modelpublicatie |
| `models.xgboostV4` | Training met technische en fundamentele features |
| `models.score_xgboost` | Scoring met de opgeslagen V3-modellen |
| `models.xLightgbmV2` | LightGBM-training en evaluatierapporten |
| `models.xgboostV3_gecontroleerd` | Afzonderlijke conservatieve trainingsvariant |
| `models.xLightgbmV2_gecontroleerd` | Afzonderlijke kandidaatproef; uitvoer in `models/lightgbmV2_controle/` |

```bash
python -m models.xLightgbmV2 --horizons 10d --geen-notify
python -m models.xLightgbmV2_gecontroleerd --help
python -m common.lightgbm_v2_preflight --database --output preflight.json
python -m maintenance.patch_xgboost_quality models/xgboostV3.py
```

De productie-V3-trainer en scorer gebruiken standaard `models/xgboostV3_<horizon>_model.pkl`. De scorer behoudt zijn expliciete override `XGBOOST_MODEL_DIR`. Training V4 en LightGBM schrijft nieuwe modelbestanden eveneens in `models/`. Analytische LightGBM-rapporten blijven in `results/`; de workflow uploadt rapporten en modelbestanden samen.


Gedeelde invoer-/kwaliteitscontroles staan in `common/model_inputs.py`, `common/model_quality_gate.py` en `common/lightgbm_v2_preflight.py`. De patchtool staat in `maintenance/`; de bijbehorende offline tests staan in `tests/`.

Workflows starten modules vanuit de repository-root. Het gebruiken van `python models/<bestand>.py` is niet de ondersteunde aanroep, omdat gedeelde imports dan niet vanzelf uit de root worden gevonden.

## XGBoost zonder versienummer

| Bestanden | Werkelijke functie | Status |
| --- | --- | --- |
| `score_xgboost.py`, `score_xgboost.yml` | Productiescoring met V3-PKL-modellen voor 10/30/60 handelsdagen | Behouden; volgt succesvolle featurebouw |
| `maintenance/patch_xgboost_quality.py`, `patch_xgboost.yml`, bijbehorende tests | Voorbereiden en testen van de gecontroleerde V3-trainer | Behouden; handmatige onderhoudsroute |


Op 20 november 2026 zijn uitsluitend de XGBoost V2-trainer op `evaluation_logs` en de bijbehorende zondagse hertrainingsworkflow verwijderd. Er was geen bijgehouden `xgboostV2_model.pkl` aanwezig. V4, V3 en de gedeelde databasehelpers blijven behouden. Er zijn geen Supabase-tabellen of gegevens verwijderd; de oude V2-code blijft beschikbaar in de Git-geschiedenis.

Op 20 november 2026 zijn ook de twee zelfstandige strategiebots `bot_01xgboost` (aandelen) en `bot_01xgboostMeta` (Forex/Twelve Data), hun handmatige workflows en hun vier JSON-model-/metadatabestanden verwijderd. Deze experimenten maakten geen deel uit van V3. V3, V4, de gecontroleerde V3-variant en LightGBM blijven behouden. Historische databaseregistraties en compatibele loggerkolommen blijven beschikbaar.

Nieuwe V3-scores krijgen een apart, in dezelfde transactie opgeslagen voorspellogboek met modelhash en vastleggingstijd. De dagelijkse evaluatie leest dit zonder oude scores te herlabelen. Zie [evaluatie](../docs/xgboost_evaluation.md).
