# Meta-modellen

Deze map groepeert het parametermodel, de Ridge-meta-modellen, meta-labelclassifiers en vergelijkingen van meta- en parameterscores. Start vanuit de hoofdmap van de repository met `python -m meta_models.<module>`; zo blijven gedeelde imports uit `common`, `features`, `common.horizons` en `model_inputs` bereikbaar.

| Module | Functie | Workflow |
| --- | --- | --- |
| `a_par_model` | Rolling IC-parametermodel; JSON-picks, gewichten en optionele opslag/melding | `a__parameter_model_production.yml` |
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
python -m meta_models.a_par_model --help
python -m meta_models.a_meta_model_score --help
python -m meta_models.a_meta_model_train --help
```

De workflows blijven onder `.github/workflows/`; hun namen, schema's, secrets, Telegraminstellingen en log-/artifactpaden zijn behouden. Resultaten blijven onder `results/` vanaf de hoofdmap staan. De eerdere losse scripts in de hoofdmap zijn verplaatst; vervang eigen oude aanroepen `python a_meta_....py` door de moduleaanroep hierboven.

Het parametermodel start via `python -m meta_models.a_par_model`. De parameterworkflow en de 10d-vergelijking gebruiken dit nieuwe pad. Ook de TradingAgents-workflow gebruikt deze moduleaanroep. Er staat geen startwrapper meer in de hoofdmap; externe aanroepen moeten eveneens `python -m meta_models.a_par_model` gebruiken. JSON-picks en gewichten blijven onder `results/a_par_model_*` staan zodat de TradingAgents-consument ze blijft vinden. De XGBoost-modellen en gedeelde hulpmiddelen blijven in de hoofdmap omdat ze ook buiten deze groep worden gebruikt. De weekly-bots vallen buiten deze herindeling. De twee classifiervarianten zijn afzonderlijk behouden; de verhuizing voegt hun gedrag niet samen.

Zie ook [invoercontrole en publicatievoorwaarden](../docs/model_inputs.md).

## Walk-forwardrapport

De workflow `a_meta_model.yml` toont het rapport in het GitHub-runoverzicht en bewaart de volledige log als artifact. Telegram krijgt dezelfde kerncijfers: testperiode, aantal testdatums en waarnemingen, gemiddelde IC, top-10%-rendement tegenover het selectiegemiddelde en de laatste vijf testdatums.

De referentie is het gemiddelde van de onderzochte selecties, geen beursindex. Rendementen zijn procenten; verschillen zijn procentpunten. Gemiddelden wegen testdatums gelijk en vormen geen samengesteld portefeuillerendement. Kleine topgroepen worden gemarkeerd. De t-stat is beschrijvend en niet gecorrigeerd voor overlappende rendementshorizonten. De rapportwijziging verandert geen training, voorspellingen, selecties of scores.


## Vergelijkingsrapporten

De drie `vergelijk_meta_par_*`-tools schrijven `folds.json` en `top_selecties.json` in hun bestaande results-map. De diagnosevariant schrijft ook `diagnose_<categorie>.json`. Records behouden kolomnamen en ISO-datums; ontbrekende waarden worden `null`. Een lege nieuwe proef vervangt oude JSON-detailrapporten door `[]`, zodat eerdere resultaten niet als nieuwe uitvoer blijven staan. De bestaande `samenvatting.json`, Telegram-samenvattingen en directory-artifacts blijven behouden. Deze tools produceren geen CSV-rapporten meer.
