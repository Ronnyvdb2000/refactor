# Modelindeling en workflowcontrole — 20 november 2026

## Nieuwe indeling

| Map | Bestanden |
| --- | --- |
| `models/` | `xgboostV2.py`, `xgboostV3.py`, `xgboostV4.py`, `xgboostV3_gecontroleerd.py`, `xLightgbmV2.py`, `xLightgbmV2_gecontroleerd.py`, `score_xgboost.py` |
| `models/` | Drie bestaande V3-PKL-bestanden en vier `model_xgboost*.json`-modellen/metadata |
| `common/` | `model_inputs.py`, `model_quality_gate.py`, `lightgbm_v2_preflight.py` |
| `maintenance/` | `patch_xgboost_quality.py` |
| `tests/` | De drie eerder losse tests voor preflight, quality gate en patchtool |
| `meta_models/` | Bestaande meta-/parametermodellen blijven hier; hun imports verwijzen naar de nieuwe common-modules |

Geen `artifacts/models/` aangemaakt. De strategieën in `strats/` blijven daar en gebruiken de verplaatste JSON-modelpaden. De forex-bot met de naam `bot_01xgboostMeta` is geen Ridge-meta-model en is daarom niet naar `meta_models/` verplaatst.

## Paden en uitvoering

Start trainings-/scoringsscripts als `python -m models.<module>` vanuit de repository-root. Alle tien betrokken bestaande workflows gebruiken de nieuwe entrypoints en dezelfde modelpaden voor controle, laden, training, artifacts en git-publicatie. De V3-scorer behoudt `XGBOOST_MODEL_DIR`; de standaard is nu `models`. V2/V3/V4 en LightGBM schrijven nieuwe productie-modelbestanden onder `models/`. LightGBM-rapporten blijven in `results/`; de gecontroleerde LightGBM-proef gebruikt een afzonderlijke `models/lightgbmV2_controle/`-map.

De zeven bestaande opgeslagen model-/metadatabestanden zijn byte voor byte vergeleken met de originelen en ongewijzigd. Training, features, targets en beoordelingsdrempels zijn niet aangepast. De modulepaden voor de gedeelde helpers en de modelbestanden zijn wel veranderd; externe scripts moeten die nieuwe paden gebruiken.

## Validatie

90 gerichte tests geslaagd. Deze controleren bestaande modelinvoer-, horizon- en kwaliteitsregels, JSON-rapporten, de nieuwe paden, atomair opslaan en opnieuw laden, behoud van een bestaand model na een ongeldige vervanging, JSON-model/metadata-roundtrips, CLI-help en patchgeneratie. Testtraining gebruikt uitsluitend kleine synthetische datasets; geen productiehertraining of Supabase-writes.

De drie verplaatste productie-V3-PKL's zijn daadwerkelijk geladen en door de bestaande bestands-/featurevalidator goedgekeurd. Bij laden verschijnt een waarschuwing over een oudere XGBoost-serialisatieversie; de validatie slaagt en deze wijziging converteert de bestaande modellen niet.

YAML en shell-syntax van alle tien aangepaste bestaande workflows gecontroleerd. De statische audit omvat nu 67 workflows, inclusief de nieuwe **Model layout and workflow checks**. De audit herkent pytest als extern testgereedschap in plaats van een ontbrekend lokaal script.

De nieuwe CI-workflow herhaalt deze offline tests, valideert de echte opgeslagen V3-bestanden en controleert de workflowcommando's op GitHub. Hij heeft alleen leesrechten en ontvangt geen Supabase- of Telegram-secrets. Een geslaagde controle is geen bewijs van de prestaties van toekomstige productietrainingen.
