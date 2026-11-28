# Gedeelde definities en dependencies

`common/model_definitions.py` bevat de geordende elf ML-features, hun relatieve
subset, ML-horizons, baseline en topfractie. V3-training, live V3-scoring,
LightGBM, CatBoost, Elastic Net en Extra Trees nemen dezelfde featurevolgorde
over. V4 behoudt zijn uitgebreidere featurecontract. De parameter-CLI gebruikt de centrale standaard scoreconfiguratie; een
regressietest controleert dat deze overeenkomt met het bevroren paperprotocol.
Overrides blijven mogelijk.

`common/costs.py` definieert basispunten en kostenprofielen:

| Toepassing | Definitie |
|---|---|
| Portefeuille | 5 bp commissie en 10 bp slippage per zijde |
| Niet-EUR conversie | 5 bp FX per zijde |
| Model-scoreanalyse | vaste aftrek van 20 bp per volledige transactie |
| Oud XGBoost-signaalrapport | standaard bruto, aftrek via `--cost-bps` |
| Factoranalyse | gevoeligheid voor 0/10/20/40 bp per volledige transactie |

De scoreaftrek is een scenario, geen geschatte werkelijke portefeuillekost.
Historische US/EUR-configuraties gebruiken dezelfde standaardtarieven; de
EUR-engine en scoreanalyses gebruiken de centrale eenhedenconversie. De bestaande koop-/verkoop-, FX- en NAV-formules blijven
behouden. Oude runs worden niet herberekend of met nieuwe instellingen overschreven.
`analyses/paper_trial.py` is een bewuste uitzondering op codeconsolidatie: zijn
broncode is als uitvoeringsengine gehasht in de bestaande proef. Die blijft
byte voor byte behouden; tests bewaken de overeenstemming van kosten en
parameterinstellingen met de centrale definities. Na een gewijzigde
trainingshash kan die proef bestaande mandjes afronden, maar zijn nieuwe
beslissingen volgens de bestaande bewaking voor een afzonderlijke proefversie.

`requirements-numerical.txt` legt numpy, pandas, scipy, scikit-learn en joblib
eenmalig vast. Analyse-, paper- en ML-profielen includeren dit bestand.
`requirements-model-io.txt` deelt PostgreSQL- en HTTP-clients. V3-training en
live-scoring installeren beide `requirements-xgboost.txt`.

Estimatorversies blijven expliciet per bestaand profiel: V3 gebruikt XGBoost
3.2.0, V4/meta-labeling 2.1.3, productie-LightGBM 4.6.0 en de geïsoleerde
ML-reconstructie LightGBM 4.7.0/XGBoost CPU 3.2.0. Dit consolideert de basis
zonder bestaande modellen stilzwijgend op een andere estimatorversie te laden.
TradingAgents behoudt een afzonderlijke omgeving wegens zijn pandas 3-contract;
het algemene `requirements.txt` voor bots valt buiten de ML-runtime.

Gedeelde code en transitieve requirements worden meegenomen in de betrokken
implementatie-/recept-hashes. Een gewijzigde definitie kan daardoor een nieuwe
run vereisen; een oude bevroren vergelijking wordt niet stilzwijgend voortgezet.
Model-layout-CI test de gedeelde contracten en de bestaande regressietests.
