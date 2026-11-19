# Repo-vervolgcontrole — 19 november 2026

Uitgangspunt: `main` op `3edf0248494ab67c8de26164355fee59a1637ca7`.
Een afzonderlijke schone checkout is gebruikt. Lokale wijzigingen in andere
checkouts, waaronder TradingAgents, zijn niet overgenomen of gewijzigd.
`refactor-lab` is niet gewijzigd.

## Resultaat

- Workflowaudit: 65 workflows, geen fouten of waarschuwingen in de gecontroleerde
  entrypoints, concurrencygroepen, joblimieten en expliciete workflow-pushpaden.
- YAML van alle 65 workflows geladen; 207 shellstappen gecontroleerd met `bash -n`
  na vervanging van Actions-expressies door een placeholder. Geen syntaxfouten.
- Volledige suite: 759 tests en 17 deeltests geslaagd, 13 optionele tests
  overgeslagen. Python 3.12; uitgaande socketverbindingen geblokkeerd tijdens de
  testrun. Een waarschuwing uit de geïnstalleerde pytest/anyio-plugin.
- Gerichte controles voor 60d, dipscan, scantempo, workflows en Yahoo-budget:
  114 tests en zeven deeltests geslaagd.
- `git diff --check` geslaagd.

De dipscan had Telegram verwijderd. Script, workflowsecrets en regressietests
zijn hersteld; lange rapporten worden opgesplitst zonder tekstverlies. E-mail
en database blijven behouden. Zie [de dipscancontrole](controle_01volhunter.md).

De alleen-lezen databasecontrole vond 9.268 60d-labels over twaalf selectiedatums.
Zie [de 60d-opvolging](controle_forward_modellen_20261115.md).

## Nog open

De workflowaudit bewijst het bestaan van de gevonden entrypoints, niet dat alle
optionele imports en providerfuncties in iedere afzonderlijke Actions-installatie
werken. De tests ondersteunen de gecontroleerde importpaden en regressies; een
volledige dependencyvalidatie in alle workflowomgevingen blijft open.

Voor de dipscan blijven live dekking en doorlooptijd open. De eigen seriële
downloader heeft nog individuele fallback/repair voor ontbrekende of ongeldige
data. De latere correctie hieronder begrenst rate-limit-herpogingen. Geen grote scan of modeltraining
gestart. De volledige repository is hiermee niet definitief afgetekend.

TradingAgents blijft buiten deze correctie; de ongepubliceerde wijzigingen zijn
niet gevalideerd of meegenomen.

## Vervolgronde vanaf `49583ca`

### Dependencies

De Python-imports van de gevonden workflow-entrypoints zijn statisch gevolgd
door lokale modules en package-initialisatie heen, inclusief imports binnen
functies. Deze inventaris is vergeleken met de pip-installaties en gebruikte
requirementsbestanden per job. De TradingAgents-workflows zijn buiten deze ronde
gehouden. De drie gevonden aandachtspunten zijn afzonderlijk beoordeeld:

| Workflow | Bevinding | Beoordeling en actie |
| --- | --- | --- |
| `analyse_correlatie_parameters.yml` | `analyse_parameter_correlatie.py` importeert `statsmodels.api`, niet geïnstalleerd via `requirements-analysis.txt` | Werkelijk ontbrekend; `statsmodels>=0.14` toegevoegd en importcontrole vóór uitvoering geplaatst |
| `score_xgboost.yml` | `model_inputs.py` importeert numpy zonder eigen pip-argument | Wordt door onder meer pandas en scikit-learn mee geïnstalleerd; geen ontbrekende dependency aangetoond |
| `weekly_runV2.yml` | Statisch pad naar psycopg2 via `weekly.shared` | Alleen via de functie voor selectieopslag; de winnaarsanalyse roept deze functie niet aan en vereist dit opslagpad niet |

Een nieuwe geïsoleerde Python 3.12-omgeving is uitsluitend met
`requirements-analysis.txt` geïnstalleerd. De imports van
`analyse_parameter_correlatie`, `meta_models.a_meta_model`,
`meta_models.a_meta_model_train` en `meta_models.a_meta_model_score` slagen,
met uitgaand socketverkeer geblokkeerd. `pip check` vindt geen conflicten.
Dit valideert de concrete dependencycorrectie; geen modelrun, databaseverbinding
of notificatie is gestart. Actions gebruikt voor deze workflows Python 3.11.
Andere versiecombinaties, dynamische imports, providerinterne dependencies en
optionele uitvoerpaden blijven buiten de garantie van deze statische controle.

### Dipscan

De eigen downloader herkende voorheen alleen opgegooide rate-limit-excepties.
Een gelogde 429 met lege/partiële tabel kon daardoor individuele fallback
activeren. De gedeelde logdetectie wordt nu rond elke yfinance-aanroep gebruikt.
Maximaal één herpoging na zestig seconden; bij herhaling stopt de requestgate
verdere aanvragen. De eerdere drie herpogingen met samen negen minuten pauze
zijn vervallen. De volledige invoer en selectievoorwaarden blijven behouden.

### Verificatie

De volledige offline suite slaagt met **763 tests en 19 deeltests**; 13 optionele
tests overgeslagen. Alle uitgaande socketverbindingen zijn tijdens die testrun
geblokkeerd. Geen grote live scan gestart; live dekking en doorlooptijd blijven open.
