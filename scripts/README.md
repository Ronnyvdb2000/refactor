# Scripts

Uitvoerbare tools voor voorbereiding en beheer van de tickerlijsten.

| Module | Doel | Workflow |
| --- | --- | --- |
| `scripts.bot_041mV2` | MRA Nitro-filter: bronlijsten beoordelen en master-, export- en delistedlijsten bijwerken | `run__filtertickersmungerV2.yml` |

Start vanuit de repository-root:

```bash
python -m scripts.bot_041mV2
```

Tickerbestanden blijven relatief aan de werkmap in de hoofdmap. De workflow behoudt zijn wekelijkse planning, handmatige start, Telegram-instellingen, Yahoo-wachtrij en publicatie van gegenereerde tickerlijsten. Importeren start geen scan; uitvoeren doet dat wel.
