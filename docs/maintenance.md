# Maintenance — backfills en handmatige controles

De twee backfills staan in `maintenance/` in `Ronnyvdb2000/refactor`. Start ze als Python-module vanuit de repository-root. De bestaande workflows blijven handmatig beschikbaar onder `.github/workflows/`.

| Script | Workflow | Standaard |
| --- | --- | --- |
| [Technische features](../maintenance/backfill_technische_features.py) | [Backfill technische features](../.github/workflows/backfill.yml) | `dry_run=true`: alleen controle/rapportage |
| [Rank-kolommen](../maintenance/backfill_rank_kolommen.py) | [Backfill rank-kolommen (eenmalig)](../.github/workflows/backfill_rank_kolommen.yml) | `apply=false`: alleen controle/rapportage |

## Starten in GitHub Actions

Kies de gewenste workflow, klik **Run workflow** en selecteer **main**. De namen van de workflows en hun invoervelden zijn behouden. Een eerste controle kan met de standaardinstellingen worden gestart. Alleen expliciet `dry_run=false` respectievelijk `apply=true` schakelt schrijven in.

De technische workflow bewaart zijn log nog steeds onder `results/backfill_log.txt` en uploadt hetzelfde Actions-artifact. Module-imports worden vooraf gecontroleerd. `pipefail` voorkomt dat `tee` een mislukte Python-uitvoering verbergt.

## Nieuwe commando's

```bash
python -m maintenance.backfill_technische_features
python -m maintenance.backfill_rank_kolommen
python -m maintenance.backfill_rank_kolommen --apply
```

De technische backfill behoudt de bestaande `BACKFILL_DRY_RUN`, `BACKFILL_BATCH_SIZE`, `BACKFILL_TICKER_FILTER` en `BACKFILL_SLEEP`-variabelen. `SUPABASE_DB_URL` wordt nog steeds via de workflow doorgegeven. Het script importeert de berekening uit `bouw_generieke_technicals.py` in de hoofdmap; de module-uitvoering zorgt dat die import blijft werken.

De oude losse scriptpaden in de hoofdmap zijn vervangen. Gebruik `--help` bij beide modules om de beschrijving te bekijken zonder een databaseverbinding te openen.

## Gebruik

Dit zijn onderhoudstaken, geen periodieke bots. De technische backfill kan nuttig zijn bij nieuwe indicatoren of ontbrekende historische features; onvoldoende koershistorie kan niet altijd worden hersteld. De rank-backfill is beschikbaar om opgeslagen JSON-waarden naar rank-kolommen over te brengen als daar opnieuw behoefte aan ontstaat.

Deze verplaatsing voert zelf geen backfill uit en verandert geen databasegegevens.

## Controle

De nieuwe modulecommando's en de shellcommando's uit beide YAML-bestanden zijn getest. Tests controleren de dry-run/apply-keuze, het behoud van de veilige technische dry-run-default en de foutcode van Python door de logpipeline. Databaseverbindingen en backfill-schrijfacties zijn hiervoor niet uitgevoerd.

## Historiecontrole technische backfill (2026-11-14)

Eén Yahoo-koersbalk levert bij EMA en MACD startwaarden op, geen bruikbare historische indicatoren. De backfill vereist daarom per feature volledige opwarming: 130 balken voor MACD, 40 voor EMA8, 100 voor EMA20 en het EMA-verschil, 70 voor ADX; rolling-indicatoren vereisen hun volledige venster (tot 252 balken). De live formules blijven behouden. Een echte nulwaarde blijft toegestaan wanneer voldoende historie beschikbaar is.

Ongeldige OHLCV-balken en onderbrekingen langer dan zeven kalenderdagen starten de opwarming opnieuw. Dubbele koersdatums geven een fout. De bestaande MA50 en RSI worden vóór iedere update gecontroleerd: minstens één vergelijkbare controle is vereist; alle aanwezige controles moeten berekenbaar zijn en binnen 2% (MA50) respectievelijk 3 punten (RSI) overeenkomen. Anders blijft de rij onaangeroerd. Bestaande niet-NULL-waarden blijven behouden.

Het log toont ontvangen koershistorie, geblokkeerde controles en ontbrekende opwarming. Download- en SQL-fouten maken de workflow na de samenvatting rood. Ontbrekende historie wordt afzonderlijk gerapporteerd. Python-uitvoer verschijnt direct, ook via de logpipeline.


## Handmatige verbinding- en koerscontroles

De vroegere losse `test_telegram.py` en `test_verbinding.py` zijn samengevoegd in `maintenance/check_telegram.py`. Het waren handmatige berichttests, geen offline unittests. Importeren en `--help` versturen geen berichten. Alleen expliciete uitvoering stuurt één bericht; ontbrekende instellingen, time-outs, HTTP-fouten en een negatieve Telegram-respons leveren exitcode 1 op. Tokens en chat-ID's worden niet afgedrukt. De bestaande workflow **Test Telegram** behoudt zijn keuzes `test_telegram`, `test_verbinding` en `beide`, maar gebruikt modulecommando's en de exitcode in plaats van zoeken in logtekst.

```bash
python -m maintenance.check_telegram --message bot
python -m maintenance.check_telegram --message verbinding
python -m maintenance.diag_koersen MOL.WA AAPL --start 2026-08-01
```

De koersdiagnose is uit de hoofdmap naar `maintenance/` verplaatst. De workflow **Diag koersen** is aangepast. Importeren start geen Yahoo-downloads. De bestaande begindatum blijft de standaard en kan nu met `--start` worden veranderd.
