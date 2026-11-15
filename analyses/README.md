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
