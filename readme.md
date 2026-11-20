# refactor

Python-repository voor aandelenscreening, technische en fundamentele features, forward returns, modeltraining en rapportage via GitHub Actions.

Deze handleiding beschrijft de code op `main`. De repository `Ronnyvdb2000/refactor-lab` is een afzonderlijk project; gebruik daarvoor eigen code, configuratie en documentatie.

## Dataflow

1. Tickerlijsten (`data/tickers_*.txt`) vormen het universum voor de scanners.
2. Strategieën (`strats/bot_*.py`) produceren selecties en rapporten.
3. Feature-builders vullen technische en fundamentele gegevens in Supabase/PostgreSQL.
4. `scripts/bouw_forward_returns.py` koppelt selecties aan latere rendementen.
5. Analyse- en modelscripts gebruiken deze gegevens voor evaluatie, training en scoring.
6. Workflows bewaren, afhankelijk van het script, CSV-bestanden, modelresultaten of andere rapporten en sturen notificaties.

De precieze invoer, uitvoer en eventuele schrijfacties verschillen per script. Lees vóór uitvoering de bijbehorende workflow en scriptopties.

## Structuur

Alle aanvullende documentatie staat in [docs/](docs/readme.md), met een overzicht van evaluaties, controlerapporten en tickercorrecties.

| Pad | Functie |
| --- | --- |
| [models/](models/) | XGBoost/LightGBM-training, scoring en opgeslagen modellen met metadata |
| [common/](common/) | Gedeelde indicatoren, koersdownloads, notificaties, tekst- en positiegroottefuncties |
| [common.py](common.py) | Compatibiliteitsbestand naast de package |
| [tests/](tests/) | Tests voor gedeelde functies en verschillende bots |
| [strats/](strats/README.md) | Alle `bot_00*`- en `bot_01*`-strategieën en scanners |
| [combiners/](combiners/) | Combineerders van strategische signalen |
| `a_*.py` | Parameter- en meta-modellen |
| `scripts/bouw_*.py` | Feature-stores en forward returns |
| [weekly/](weekly/) | Weekrapporten a/x met gedeelde winnaaropslag, winnaarsanalyse en weekly-correlaties |
| [data/](data/) | Tickeruniversums, filter-masterlijsten, actieve lijsten en delisted-caches |
| [scripts/](scripts/) | Uitvoerbare tools voor tickerlijstvoorbereiding |
| [maintenance/](maintenance/) | Handmatige backfills en onderhoud |
| `analyse_*.py`, `analyseer_*.py` | Rendements-, parameter- en correlatieanalyses |
| [.github/workflows/](.github/workflows/) | Planning, dependencies, secrets en uitvoercommando's per taak |
| [EVALUATION.md](docs/EVALUATION.md) | Eerdere architectuurevaluatie; voortgangsclaims daarin opnieuw controleren |
| [docs/controles/](docs/controles/) | Gerichte controlerapporten van afzonderlijke bots |

### Gedeelde functies

Importeer gedeelde functies via `from common import ...`. Deze exports worden pas bij gebruik geladen; `import common` laadt geen koersprovider of notificatiebibliotheek. De publieke exports staan in [common/__init__.py](common/__init__.py).

- [indicators.py](common/indicators.py): onder meer RSI, ATR, ADX, MACD, Bollinger Bands, stochastic, IBS en voortschrijdende gemiddelden.
- [io.py](common/io.py): tickerlijsten en `download_history`.
- [notify.py](common/notify.py): Telegram en e-mail.
- [sizing.py](common/sizing.py): positiegrootte, ATR-stops en transactiekosten.
- [features.py](common/features.py): gedeelde featurelijsten, modelhorizon en doelkolom voor analyses en meta-modellen.
- [db_logger.py](common/db_logger.py): gedeelde opslag van selecties in Supabase, inclusief controles tegen dubbele invoer.
- [fundamentals.py](common/fundamentals.py): beschikbaarheidscontrole voor opgeslagen fundamentals.
- [horizons.py](common/horizons.py): horizons en kalenderembargo.
- [reports.py](common/reports.py): JSON-rapporten en optionele CSV-export.
- [text.py](common/text.py): veilige conversies, opmaak en links.

Niet ieder script gebruikt uitsluitend deze package. Controleer bij aanpassingen ook lokale implementaties en constanten. Het gedeelde kostenmodel gebruikt momenteel `max(15.0, bedrag * 0.0035)` voor een positief bedrag; dit is geen vaste kost plus percentage. Slippage is een afzonderlijke constante en wordt door `trade_cost` niet toegevoegd.

De combineerders staan in [combiners/](combiners/) en starten als Python-modules vanuit de hoofdmap. Zie [de controle en uitvoerinstructies](docs/combiners.md).

De weekly scripts starten eveneens als modules vanuit de hoofdmap. Zie [weekly: uitvoering en controle](docs/weekly.md). Beide rapporten bewaren hun eigen topvijf-stijgers boven 10% met historische parameters; overlap krijgt bronselectie `a,x`. De losse opvolgscan is vervallen. Telegram blijft behouden.

## Strategieën en modellen

De scanners omvatten onder meer CAN SLIM, Darvas Box, Dual Momentum, Minervini, Graham, Greenblatt, O'Shaughnessy, Fisher, VCP, mean reversion, Beurssignalen, co-integratie en verschillende XGBoost-varianten.

Belangrijke vervolgstappen in de pipeline:

| Script | Rol |
| --- | --- |
| [bot_041mV2.py](scripts/bot_041mV2.py) | Tickerselectie V2 |
| [a_trade.py](combiners/a_trade.py), [a_trade_q.py](combiners/a_trade_q.py), [a_trade_combi.py](combiners/a_trade_combi.py) | Combineren en rangschikken van signalen |
| [a_par_model.py](meta_models/a_par_model.py) | Parametermodel |
| [a_meta_model.py](meta_models/a_meta_model.py) | Evaluatie van het meta-model |
| [a_meta_model_train.py](meta_models/a_meta_model_train.py), [a_meta_model_score.py](meta_models/a_meta_model_score.py) | Training en scoring van het meta-model |
| [score_xgboost.py](models/score_xgboost.py) | XGBoost-scoring |
| [xLightgbmV2.py](models/xLightgbmV2.py) | LightGBM V2-training |
| [vergelijk_meta_par_horizons.py](meta_models/vergelijk_meta_par_horizons.py) | Vergelijking over rendementshorizonten |

De meta-modellen en hun vergelijkingsscripts staan in [meta_models/](meta_models/README.md). Start ze vanuit de hoofdmap met `python -m meta_models.<scriptnaam>`; de workflows gebruiken deze moduleaanroep.

De handmatige technische opzoektool [A Top tickers](analyses/a_top_dist_sma50.py) staat in [analyses/](analyses/README.md). De workflow `run_top_dist_sma50.yml` start deze met `python -m analyses.a_top_dist_sma50`.

### Feature-stores

- [bouw_generieke_technicals.py](scripts/bouw_generieke_technicals.py): technische features in `generieke_technicals`.
- [bouw_generieke_fundamentals.py](scripts/bouw_generieke_fundamentals.py): fundamentele gegevens in `generieke_fundamentals`.
- [bouw_forward_returns.py](scripts/bouw_forward_returns.py): labels in `forward_returns`; de workflow vraagt horizons 5, 10, 20, 30 en 60 handelsdagen aan. Alleen afgeronde koersdagen tellen mee; bestaande labels worden bij aanvulling behouden. Zie de [controle van forward returns en modelhorizons](docs/controles/controle_forward_modellen_20261115.md).

Forward returns zijn pas beschikbaar nadat de betreffende horizon verstreken is. Veel tickers op weinig datums vervangen geen langere historie. Controleer ook dat koersaanpassingen, peildatums en rendementseenheden overeenkomen tussen builders en modellen.

## LightGBM V2

De workflow [XLightGBM V2 Trading Model](.github/workflows/run_02XLightGBM%20V2%20Trading.yml) start `models.xLightgbmV2` met Python 3.11. In deze workflow is alleen handmatige uitvoering (`workflow_dispatch`) actief; de wekelijkse planning staat uitgecommentarieerd.

Het model:

- gebruikt standaard de horizons `10d,30d,60d`;
- koppelt `generieke_technicals` en gededupliceerde `forward_returns` op ticker en datum;
- gebruikt elf technische features;
- voorspelt standaard of het rendement boven de mediaan van dezelfde datum ligt (`--target excess`);
- ondersteunt `--target abs` voor een positief absoluut rendement;
- splitst chronologisch, met standaard een embargo van `ceil(horizon * 7 / 5) + 2` kalenderdagen;
- rapporteert onder meer AUC, IC en top-20%-rendementen.

De rendementsopmaak in dit script verwacht forward returns in procentpunten: `2.5` wordt `2.5%`. Fracties zoals accuracy worden afzonderlijk met 100 vermenigvuldigd.

### Eerst databeschikbaarheid controleren

[lightgbm_v2_preflight.py](common/lightgbm_v2_preflight.py) controleert de buitenste train/test-split zonder training of schrijfacties naar Supabase:

```bash
python -m common.lightgbm_v2_preflight --database --output preflight.json
```

Of met een CSV met `ticker`, `datum`, de elf features en de drie forward-returnkolommen:

```bash
python -m common.lightgbm_v2_preflight --csv trainingsdata.csv
```

`WAIT_DATA` betekent dat meer bruikbare historie nodig is. `READY` betekent alleen dat de gecontroleerde buitenste split uitvoerbaar is; interne validatie en modelkwaliteit moeten daarna nog worden beoordeeld. Een geslaagde workflow bewijst op zichzelf niet dat alle horizons een model hebben opgeleverd.

### Training uitvoeren

Installeer eerst de dependencies uit de V2-workflow en stel `SUPABASE_DB_URL` in.

```bash
python -m models.xLightgbmV2 --horizons 10d --geen-notify
python -m models.xLightgbmV2 --geen-notify
```

Modelbundels komen in `models/`; rapporten standaard in `results/` (rapportmap aanpasbaar via `RESULTS_DIR`):

- `models/lightgbmV2_<h>_model.pkl`
- `lightgbmV2_<h>_feature_importance.json`
- `lightgbmV2_<h>_correlations.json`
- `lightgbmV2_<h>_test_predictions.json`
- `lightgbmV2_summary.txt`

Een modelbundel bevat `model` voor evaluatie en `model_full` voor productie. De workflow uploadt de rapporten uit `results/` en de modelbundels uit `models/` als Actions-artifact met een bewaartermijn van 30 dagen. Controleer de samenvatting en bestanden per horizon.

## Installatie en configuratie

Voer scripts vanuit de repository-root uit, zodat relatieve ticker- en uitvoerpaden kloppen. Python 3.11 wordt onder meer door de V2- en feature-storeworkflows gebruikt.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Kies daarna de dependencies van de specifieke workflow. [requirements.txt](requirements.txt) en [requirements-analysis.txt](requirements-analysis.txt) zijn geen volledige dependencylijst voor alle scripts. De V2-workflow installeert bijvoorbeeld ook LightGBM, SciPy, scikit-learn en joblib met vastgelegde versies.

Veelgebruikte omgevingsvariabelen:

| Variabele | Gebruik |
| --- | --- |
| `SUPABASE_DB_URL` | PostgreSQL-verbinding voor scripts die Supabase gebruiken |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram-notificaties waar ingeschakeld |
| `EMAIL_USER`, `EMAIL_PASS`, `EMAIL_RECEIVER` | E-mailrapporten waar ingeschakeld |
| `RUN_URL` | Link naar de Actions-run, onder meer in V2 |
| `RESULTS_DIR` | Alternatieve resultaatmap voor LightGBM V2 |

De vereiste variabelen en extra providerconfiguratie verschillen per workflow. Bewaar geheime waarden in GitHub Actions Secrets of de lokale omgeving, niet in broncode.

## GitHub Actions

Zie het [volledige workflowoverzicht](docs/workflow_planning.md) en de [controle van runbeveiliging, planning en Git-publicatie](docs/controles/controle_workflow_planning_20261115.md).

De repository bevat zowel geplande als handmatig startbare workflows. De YAML-bestanden beschrijven de triggers; de feitelijke ingeschakelde status en recente uitkomsten controleer je op de [Actions-pagina](https://github.com/Ronnyvdb2000/refactor/actions).

Voorbeelden van de huidige YAML-configuratie:

| Taak | Planning / uitvoering |
| --- | --- |
| Generieke technicals | Dagelijks 22:15 UTC en handmatig; legt een feature-peildatum en bronmanifest vast |
| Forward returns | Dagelijks 22:00 UTC en handmatig; commando `python -m scripts.bouw_forward_returns build` |
| Kritische selectie | Maandag–vrijdag 19:45 UTC en handmatig; commando `python -m strats.bot_00kr live` |
| LightGBM V2 | Handmatig |

Geselecteerde Yahoo-jobs delen [vier aparte wachtrijen](docs/yahoo_budget.md), zodat lange scans de dagelijkse, feature- en tijdgebonden groepen niet bezet houden. De gedeelde koersdownloader gebruikt maximaal vier workers.

Productie-XGBoost- en Ridge-meta-scoring starten automatisch na een geslaagde technicals-bouwrun op `main`, met het manifest en de peildatum van die bronrun. Nieuwere selecties wachten op een volgende bouwrun; per toegelaten datum blijven de bestaande dekkingscontroles vereist. De zondagse meta-hertraining blijft apart. Zie [de dagdataketen](docs/feature_pipeline.md).

Ridge-meta- en productie-XGBoost-scoring controleren [datum en invoerdekking vóór publicatie](docs/model_inputs.md). Voor weekendselecties wordt uitsluitend de voorafgaande vrijdag als koersdatum gebruikt; beide datums worden vermeld. Op werkdagen blijft dezelfde datum vereist.

Cron-tijden zijn UTC; de Belgische lokale tijd verschilt met zomer- en wintertijd. Losse tijdschema's garanderen geen afhankelijkheidsvolgorde: controleer voltooiing van de benodigde databouw vóór een modelrun.

Sommige workflows schrijven CSV-bestanden terug naar GitHub en hebben daarom `contents: write`; andere gebruiken alleen leestoegang. Vermijd gelijktijdige runs die dezelfde bestanden of databasegegevens wijzigen.

## Tests en onderhoud

Installeer `pytest` en de dependencies voor de te testen modules.

```bash
python -m pytest tests/ -v
python -m pytest tests/test_lightgbm_v2_preflight.py -v
```

De testverzameling groeit; deze handleiding legt geen vast aantal geslaagde tests vast. Een import of een live-run is geen vervanging voor tests en kan bij scripts met uitvoer op moduleniveau neveneffecten hebben.

Bij verdere wijzigingen:

- Controleer eerst het concrete script én de bijbehorende workflow.
- Beoordeel modelresultaten per horizon, datumbereik, embargo en hoeveelheid onafhankelijke historie.
- Houd rekening met ontbrekende of gewijzigde Yahoo Finance-data.
- Controleer per analyse hoe overlappende rendementen en meerdere toetsen worden behandeld.
- Controleer database- en bestandsgebruik voordat dezelfde pipeline vanuit een andere repository wordt gestart.
- Gebruik `docs/controles/controle_*.md` en `docs/EVALUATION.md` als achtergrond; actuele code en nieuwe controles bepalen de huidige status.
