# Meta-modellen

Deze map groepeert de Ridge-meta-modellen, meta-labelclassifiers en vergelijkingen van meta- en parameterscores. Start vanuit de hoofdmap van de repository met `python -m meta_models.<module>`; zo blijven gedeelde imports uit `common`, `features`, `horizon_utils` en `model_inputs` bereikbaar.

| Module | Functie | Workflow |
| --- | --- | --- |
| `a_meta_model` | Ridge walk-forwardevaluatie | `a_meta_model.yml` |
| `a_meta_model_train` | Ridge trainen en model bewaren | `a__meta_model_production.yml` |
| `a_meta_model_score` | Scoren met opgeslagen Ridge-model | `a__meta_model_production.yml` |
| `a_meta_label_classifier` | Meta-labelanalyse | `a_meta_labeling_classifier.yml` |
| `a_meta_file_classifier` | Bestaande afzonderlijke classifiervariant; handmatig | Geen gekoppelde workflow |
| `vergelijk_meta_par_10d` | Vergelijking op 10 dagen | `vergelijk_meta_par_10d.yml` |
| `vergelijk_meta_par_horizons` | Vergelijking over meerdere horizons | `vergelijk_meta_par_horizons.yml` |
| `vergelijk_meta_par_horizons_diagnose` | Diagnose van horizonvergelijkingen | `vergelijk_meta_par_horizons_diagnose.yml` |

Bijvoorbeeld:

```bash
python -m meta_models.a_meta_model_score --help
python -m meta_models.a_meta_model_train --help
```

De workflows blijven onder `.github/workflows/`; hun namen, schema's, secrets, Telegraminstellingen en log-/artifactpaden zijn behouden. Resultaten blijven onder `results/` vanaf de hoofdmap staan. De eerdere losse scripts in de hoofdmap zijn verplaatst; vervang eigen oude aanroepen `python a_meta_....py` door de moduleaanroep hierboven.

`a_par_model.py`, de XGBoost-modellen en de gedeelde hulpmiddelen blijven in de hoofdmap omdat ze ook buiten deze groep worden gebruikt. De weekly-bots vallen buiten deze herindeling. De twee classifiervarianten zijn afzonderlijk behouden; de verhuizing voegt hun gedrag niet samen.

Zie ook [invoercontrole en publicatievoorwaarden](../docs/model_inputs.md).
