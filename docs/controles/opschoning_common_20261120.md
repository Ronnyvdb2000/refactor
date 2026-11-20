# Gedeelde helpers — 20 november 2026

De gedeelde utils-bestanden uit de hoofdmap zijn verplaatst:

| Voorheen | Nu |
| --- | --- |
| `fundamentals_utils.py` | `common/fundamentals.py` |
| `horizon_utils.py` | `common/horizons.py` |
| `report_utils.py` | `common/reports.py` |

Alle interne Python-imports en tests gebruiken de nieuwe modulepaden. De oude bestanden zijn verwijderd; externe scripts die daaruit importeren moeten hun imports aanpassen. `common.exports` blijft als compatibiliteitsinterface voor bestaande CSV-aanroepen bestaan en verwijst nu naar `common.reports`.

## Lichte package-import

`common/__init__.py` gebruikt uitgestelde imports. Bestaande publieke namen en `__all__` zijn behouden: `from common import download_history` krijgt dezelfde functie uit `common.io`, maar alleen de gevraagde module wordt geladen. De functie wordt na het opvragen op de package vastgelegd. Ook `from common import io`, directe submodule-imports en `from common import *` blijven ondersteund. Een wildcard-import laadt uiteraard alle publieke exports en vereist hun dependencies.

`import common`, horizons, fundamentals en rapporthelpers laden zelf geen pandas, numpy, requests of yfinance. Zodra een koersdownload, indicator of notificatie wordt opgevraagd, blijven de bijbehorende dependencies nodig. De inhoudelijke berekeningen en kosten-/exportinstellingen zijn niet veranderd.

## Validatie

199 gerichte tests geslaagd; 1 bestaande test overgeslagen. Gecontroleerd zijn onder meer imports met geblokkeerde provider-/scientific dependencies, publieke exportidentiteit, wildcard- en submodule-imports, JSON- en optionele CSV-rapporten, horizons, fundamentals, XGBoost-horizons, indicatoren, sizing, invoer, Telegram-rapportage, downloadbegrenzing, combiners, modelinvoer en workflowplanning. Er zijn geen live scans, modeltrainingen, databasewrites of Telegram-verzendingen gestart.

De audit van 66 workflows meldt geen fouten of waarschuwingen. De workflowcommando's hoefden niet te veranderen: uitvoerbare scripts blijven op hun bestaande plaats. De aparte researchbranch en haar dagelijkse planning zijn niet gewijzigd.
