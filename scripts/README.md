# Scripts

Uitvoerbare tools voor voorbereiding en beheer van de tickerlijsten.

| Module | Doel | Workflow |
| --- | --- | --- |
| `scripts.bouw_generieke_technicals` | Technische indicatoren in de feature-store opbouwen | `run_generieke_technicals.yml` |
| `scripts.bouw_generieke_fundamentals` | Fundamentele gegevens opbouwen | `run_generieke_fundamentals.yml` |
| `scripts.bouw_forward_returns` | Latere rendementen voor evaluatie en training opbouwen | `run_forward_returns.yml` |
| `scripts.bot_041mV2` | MRA Nitro-filter: bronlijsten beoordelen en master-, export- en delistedlijsten bijwerken | `run__filtertickersmungerV2.yml` |

Start vanuit de repository-root:

```bash
python -m scripts.bot_041mV2
```

Tickerbestanden staan onder `data/`, relatief aan de repository-root. De workflow behoudt zijn wekelijkse planning, handmatige start, Telegram-instellingen, Yahoo-wachtrij en publicatie van gegenereerde tickerlijsten. Importeren start geen scan; uitvoeren doet dat wel.


De bouwscripts starten eveneens vanuit de hoofdmap:

```bash
python -m scripts.bouw_generieke_technicals
python -m scripts.bouw_generieke_fundamentals build
python -m scripts.bouw_forward_returns build
```

Deze commando's voeren de bestaande scans en databaseupdates uit. Planning, secrets, rapportpaden en berekeningen zijn bij de verplaatsing behouden. De technische backfill importeert de indicatorberekening via `scripts.bouw_generieke_technicals`.
