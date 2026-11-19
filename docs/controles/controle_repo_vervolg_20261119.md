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
downloader heeft nog individuele fallback/repair en drie rate-limit-herpogingen.
Deze controle wijzigt het downloadbeleid niet. Geen grote scan of modeltraining
gestart. De volledige repository is hiermee niet definitief afgetekend.

TradingAgents blijft buiten deze correctie; de ongepubliceerde wijzigingen zijn
niet gevalideerd of meegenomen.
