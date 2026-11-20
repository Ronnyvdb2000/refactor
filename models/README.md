# Models

Deze map bevat XGBoost- en LightGBM-training/scoring én de opgeslagen PKL- en JSON-modellen. Er is geen aparte artifacts-map. Voer de commando's uit vanuit de repository-root.

| Module | Functie |
| --- | --- |
| `models.xgboostV2` | Training op evaluation_logs |
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

De productie-V3-trainer en scorer gebruiken standaard `models/xgboostV3_<horizon>_model.pkl`. De scorer behoudt zijn expliciete override `XGBOOST_MODEL_DIR`. Training V2/V4 en LightGBM schrijft nieuwe modelbestanden eveneens in `models/`. Analytische LightGBM-rapporten blijven in `results/`; de workflow uploadt rapporten en modelbestanden samen.

De opgeslagen `model_xgboost*.json`-bestanden en hun metadata staan hier. De strategieën `strats.bot_01xgboost` en `strats.bot_01xgboostMeta` blijven in `strats/` en laden/schrijven deze nieuwe paden. Ondanks de naam is de tweede een forex-strategie via Twelve Data, geen Ridge-/parameter-meta-model. Die laatste modellen blijven in `meta_models/`, met hun bestaande databaseopslag en rapportpaden.

Gedeelde invoer-/kwaliteitscontroles staan in `common/model_inputs.py`, `common/model_quality_gate.py` en `common/lightgbm_v2_preflight.py`. De patchtool staat in `maintenance/`; de bijbehorende offline tests staan in `tests/`.

Workflows starten modules vanuit de repository-root. Het gebruiken van `python models/<bestand>.py` is niet de ondersteunde aanroep, omdat gedeelde imports dan niet vanzelf uit de root worden gevonden.
