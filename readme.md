# refactor

Python-repository voor aandelenscreening, technische en fundamentele features, forward returns, modeltraining en rapportage via GitHub Actions.

Deze handleiding beschrijft de code op `main`. De repository `Ronnyvdb2000/refactor-lab` is een afzonderlijk project; gebruik daarvoor eigen code, configuratie en documentatie.

## Dataflow

1. Tickerlijsten (`tickers_*.txt`) vormen het universum voor de scanners.
2. Strategieën (`bot_*.py`) produceren selecties en rapporten.
3. Feature-builders vullen technische en fundamentele gegevens in Supabase/PostgreSQL.
4. `bouw_forward_returns.py` koppelt selecties aan latere rendementen.
5. Analyse- en modelscripts gebruiken deze gegevens voor evaluatie, training en scoring.
6. Workflows bewaren, afhankelijk van het script, CSV-bestanden, modelresultaten of andere rapporten en sturen notificaties.

De precieze invoer, uitvoer en eventuele schrijfacties verschillen per script. Lees vóór uitvoering de bijbehorende workflow en scriptopties.

## Structuur

| Pad | Functie |
| --- | --- |
| [common/](common/) | Gedeelde indicatoren, koersdownloads, notificaties, tekst- en positiegroottefuncties |
| [common.py](common.py) | Compatibiliteitsbestand naast de package |
| [tests/](tests/) | Tests voor gedeelde functies en verschillende bots |
| `bot_*.py` | Strategieën en scanners |
| `a_*.py` | Combineerders, parameter- en meta-modellen |
| `bouw_*.py` | Feature-stores en forward returns |
| `analyse_*.py`, `analyseer_*.py` | Rendements-, parameter- en correlatieanalyses |
| [.github/workflows/](.github/workflows/) | Planning, dependencies, secrets en uitvoercommando's per taak |
| [EVALUATION.md](EVALUATION.md) | Eerdere architectuurevaluatie; voortgangsclaims daarin opnieuw controleren |
| `controle_*.md` | Gerichte controlerapporten van afzonderlijke bots |

### Gedeelde functies

Importeer gedeelde functies via `from common import ...`. De publieke exports staan in [common/__init__.py](common/__init__.py).

- [indicators.py](common/indicators.py): onder meer RSI, ATR, ADX, MACD, Bollinger Bands, stochastic, IBS en voortschrijdende gemiddelden.
- [io.py](common/io.py): tickerlijsten en `download_history`.
- [notify.py](common/notify.py): Telegram en e-mail.
- [sizing.py](common/sizing.py): positiegrootte, ATR-stops en transactiekosten.
- [text.py](common/text.py): veilige conversies, opmaak en links.

Niet ieder script gebruikt uitsluitend deze package. Controleer bij aanpassingen ook lokale implementaties en constanten. Het gedeelde kostenmodel gebruikt momenteel `max(15.0, bedrag * 0.0035)` voor een positief bedrag; dit is geen vaste kost plus percentage. Slippage is een afzonderlijke constante en wordt door `trade_cost` niet toegevoegd.

## Strategieën en modellen

De scanners omvatten onder meer CAN SLIM, Darvas Box, Dual Momentum, Minervini, Graham, Greenblatt, O'Shaughnessy, Fisher, VCP, mean reversion, Beurssignalen, co-integratie en verschillende XGBoost-varianten.

Belangrijke vervolgstappen in de pipeline:

| Script | Rol |
| --- | --- |
| [bot_041mV2.py](bot_041mV2.py) | Tickerselectie V2 |
| [a_trade.py](a_trade.py), [a_trade_q.py](a_trade_q.py), [a_trade_combi.py](a_trade_combi.py) | Combineren en rangschikken van signalen |
| [a_par_model.py](a_par_model.py) | Parametermodel |
| [a_meta_model.py](a_meta_model.py) | Evaluatie van het meta-model |
| [a_meta_model_train.py](a_meta_model_train.py), [a_meta_model_score.py](a_meta_model_score.py) | Training en scoring van het meta-model |
| [score_xgboost.py](score_xgboost.py) | XGBoost-scoring |
| [xLightgbmV2.py](xLightgbmV2.py) | LightGBM V2-training |
| [vergelijk_meta_par_horizons.py](vergelijk_meta_par_horizons.py) | Vergelijking over rendementshorizonten |

### Feature-stores

- [bouw_generieke_technicals.py](bouw_generieke_technicals.py): technische features in `generieke_technicals`.
- [bouw_generieke_fundamentals.py](bouw_generieke_fundamentals.py): fundamentele gegevens in `generieke_fundamentals`.
- [bouw_forward_returns.py](bouw_forward_returns.py): labels in `forward_returns`; de workflow vraagt horizons 5, 10, 20, 30 en 60 aan.

Forward returns zijn pas beschikbaar nadat de betreffende horizon verstreken is. Veel tickers op weinig datums vervangen geen langere historie. Controleer ook dat koersaanpassingen, peildatums en rendementseenheden overeenkomen tussen builders en modellen.

## LightGBM V2

De workflow [XLightGBM V2 Trading Model](.github/workflows/run_02XLightGBM%20V2%20Trading.yml) start `xLightgbmV2.py` met Python 3.11. In deze workflow is alleen handmatige uitvoering (`workflow_dispatch`) actief; de wekelijkse planning staat uitgecommentarieerd.

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

[lightgbm_v2_preflight.py](lightgbm_v2_preflight.py) controleert de buitenste train/test-split zonder training of schrijfacties naar Supabase:

```bash
python lightgbm_v2_preflight.py --database --output preflight.json
```

Of met een CSV met `ticker`, `datum`, de elf features en de drie forward-returnkolommen:

```bash
python lightgbm_v2_preflight.py --csv trainingsdata.csv
```

`WAIT_DATA` betekent dat meer bruikbare historie nodig is. `READY` betekent alleen dat de gecontroleerde buitenste split uitvoerbaar is; interne validatie en modelkwaliteit moeten daarna nog worden beoordeeld. Een geslaagde workflow bewijst op zichzelf niet dat alle horizons een model hebben opgeleverd.

### Training uitvoeren

Installeer eerst de dependencies uit de V2-workflow en stel `SUPABASE_DB_URL` in.

```bash
python xLightgbmV2.py --horizons 10d --geen-notify
python xLightgbmV2.py --geen-notify
```

Resultaten komen standaard in `results/` (aanpasbaar via `RESULTS_DIR`):

- `lightgbmV2_<h>_model.pkl`
- `lightgbmV2_<h>_feature_importance.csv`
- `lightgbmV2_<h>_correlations.csv`
- `lightgbmV2_<h>_test_predictions.csv`
- `lightgbmV2_summary.txt`

Een modelbundel bevat `model` voor evaluatie en `model_full` voor productie. De workflow uploadt `results/` als Actions-artifact met een bewaartermijn van 30 dagen. Controleer de samenvatting en bestanden per horizon.

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

De repository bevat zowel geplande als handmatig startbare workflows. De YAML-bestanden beschrijven de triggers; de feitelijke ingeschakelde status en recente uitkomsten controleer je op de [Actions-pagina](https://github.com/Ronnyvdb2000/refactor/actions).

Voorbeelden van de huidige YAML-configuratie:

| Taak | Planning / uitvoering |
| --- | --- |
| Generieke technicals | Dagelijks 21:00 UTC en handmatig |
| Forward returns | Dagelijks 22:00 UTC en handmatig; commando `python bouw_forward_returns.py build` |
| Kritische selectie | Maandag–vrijdag 19:45 UTC en handmatig; commando `python bot_00kr.py live` |
| LightGBM V2 | Handmatig |

Cron-tijden zijn UTC; de Belgische lokale tijd verschilt met zomer- en wintertijd. Losse tijdschema's garanderen geen afhankelijkheidsvolgorde: controleer voltooiing van de benodigde databouw vóór een modelrun.

Sommige workflows schrijven CSV-bestanden terug naar GitHub en hebben daarom `contents: write`; andere gebruiken alleen leestoegang. Vermijd gelijktijdige runs die dezelfde bestanden of databasegegevens wijzigen.

## Tests en onderhoud

Installeer `pytest` en de dependencies voor de te testen modules.

```bash
python -m pytest tests/ -v
python -m pytest test_lightgbm_v2_preflight.py -v
```

De testverzameling groeit; deze handleiding legt geen vast aantal geslaagde tests vast. Een import of een live-run is geen vervanging voor tests en kan bij scripts met uitvoer op moduleniveau neveneffecten hebben.

Bij verdere wijzigingen:

- Controleer eerst het concrete script én de bijbehorende workflow.
- Beoordeel modelresultaten per horizon, datumbereik, embargo en hoeveelheid onafhankelijke historie.
- Houd rekening met ontbrekende of gewijzigde Yahoo Finance-data.
- Controleer per analyse hoe overlappende rendementen en meerdere toetsen worden behandeld.
- Controleer database- en bestandsgebruik voordat dezelfde pipeline vanuit een andere repository wordt gestart.
- Gebruik `controle_*.md` en `EVALUATION.md` als achtergrond; actuele code en nieuwe controles bepalen de huidige status.
