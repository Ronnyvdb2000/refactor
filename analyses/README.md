# Analyses

Analyse- en opzoektools voor bestaande gegevens.

| Script | Doel | Workflow |
| --- | --- | --- |
| `a_top_dist_sma50.py` | Tickers rangschikken op laagste afstand tot MA50 | `run_top_dist_sma50.yml` — handmatig |

Start vanuit de hoofdmap van de repository:

```bash
python -m analyses.a_top_dist_sma50 --help
python -m analyses.a_top_dist_sma50 --dagen 15 --top 10 --telegram --email
```

De moduleaanroep houdt de gedeelde imports uit `common` bereikbaar. De workflow blijft in `.github/workflows/`; inputs, secrets, Telegram en e-mail zijn behouden. De tool leest de database zonder selecties of scores te wijzigen. Een optioneel CSV-pad blijft relatief aan de huidige werkmap.


## Analyseprogramma's

Start vanuit de repository-root met `python -m analyses.<module>`.

| Module | Doel |
| --- | --- |
| `analyse_selecties` | Aanvullende rendementsrapportage sinds selectie; variabele looptijden |
| `analyse_forward_returns` | Strategieën vergelijken op vaste rendementshorizons |
| `analyse_parameter_ic` | IC-matrix per parameter en horizon |
| `analyse_parameter_correlatie` | Parameteronderzoek met correctie voor overlappende rendementen |
| `analyseer_forward_correlaties` | Langere rendementen van weekly-selecties onderzoeken |

Deze vijf bestanden zijn verplaatst uit de hoofdmap; er blijven geen wrappers
achter. De workflows gebruiken moduleaanroepen. Uitvoerpaden onder `results/`,
argumenten, databasegedrag, Telegram en e-mail blijven behouden. De scripts
onderzoeken verschillende vragen en zijn niet samengevoegd. De laatste analyse
gebruikt weekly-data, maar beheert geen weekly-selecties of weekly-opslag.
