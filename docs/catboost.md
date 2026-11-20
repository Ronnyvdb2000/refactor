# CatBoost V1: kandidaat-evaluatie

CatBoost is een afzonderlijke handmatige proef op `main`. Op de bekeken branches,
inclusief `feature/quantify-principles`, stond geen CatBoost-code. Deze module is
zelfstandig toegevoegd; er is geen branch samengevoegd.

## Uitvoeren

Start de workflow [CatBoost V1](../.github/workflows/catboostV1_gecontroleerd.yml)
via Actions, of voer vanuit de repository-root uit:

```bash
python -m pip install -r requirements-catboost.txt
python -m models.catboostV1 --horizons 10d,30d,60d --target excess --telegram
```

`SUPABASE_DB_URL` levert invoer uit `generieke_technicals` en `forward_returns`.
De verbinding gebruikt een alleen-lezen transactie met een querylimiet van vijf
minuten. Voor een lokale proef ondersteunt `--csv invoer.csv` dezelfde kolommen.
Telegram is optioneel via `--telegram`; de workflow gebruikt de bestaande
`TELEGRAM_TOKEN` en `TELEGRAM_CHAT_ID` secrets.

## Methode

- Dezelfde elf numerieke kenmerken, complete invoerrijen en labels als de
  gecontroleerde LightGBM-variant. Er worden geen ticker-ID's als kenmerken gebruikt.
- Standaard `excess`: rendement boven de mediaan van dezelfde datum. Dit betekent
  beter dan het invoercohort, niet noodzakelijk winst of beter dan een beursindex.
  `abs` gebruikt rendement boven nul, zoals V3.
- Chronologische 80/20-splitsing op unieke datums. Een kalenderembargo van
  `ceil(horizon * 7/5) + 2` dagen scheidt training van test; dit is een benadering,
  geen exacte beurskalender. Minimaal 20 datums, 100 trainingsrijen, 30 testrijen
  en vijf testdatums. Die datums zijn door overlappende horizons niet onafhankelijk.
- Vaste hyperparameters en 400 bomen. De testset wordt nooit voor fitting,
  early stopping of parameterkeuze gebruikt. Een afzonderlijk model met relatieve
  kenmerken controleert de invloed van absolute koersniveaus.
- AUC over de testset; daarnaast top-20%-rendement en MA50-baseline per testdatum,
  daarna gelijk gewogen over datums. Dit verschilt van de historische LightGBM-
  en V3-topselectie over hun volledige testset: de cijfers zijn niet direct vergelijkbaar.
- De gedeelde kwaliteitspoort vereist AUC minimaal 0,55, hoger toprendement dan
  de baseline en maximaal 0,10 AUC-voorsprong op het relatieve controlemodel.

## Uitvoer en grenzen

JSON-rapporten en testvoorspellingen staan in `models/catboostV1_controle/`.
Alleen bij `ELIGIBLE_FOR_REVIEW` wordt een kandidaat als `.cbm` opgeslagen,
opnieuw geladen en op identieke voorspellingen gecontroleerd. Metadata bevat
kenmerken, doel, splitsing, modelhash en workflowcommit/run. `HOLD` bewaart wel
de evaluatie, maar geen nieuw model. Een eerdere kandidaat voor dezelfde horizon
wordt aan het begin van de proef verwijderd zodat die niet als nieuw resultaat geldt.
De workflow bewaart uitvoer 30 dagen als artifact; geen gegenereerde uitvoer wordt
naar Git gecommit. Er wordt geen CSV geschreven.

`WAIT_DATA` betekent onvoldoende historie/klassevariatie; `ERROR` betekent
ongeldige invoer of een technische fout en geeft een mislukte run. Het rapport
blijft zichtbaar, ook als niet alle horizons kunnen worden beoordeeld.

Er worden geen productiemodellen of scores vervangen en geen databasegegevens
geschreven. De bestaande V3-scorer en combiners gebruiken CatBoost niet automatisch.
Een positief advies is uitsluitend een kandidaat voor review. De rendementen zijn
historisch en bruto, zonder kosten, portefeuille-simulatie of bewijs van toekomstige
meerwaarde. V3, V4 en LightGBM moeten voor een eerlijke vergelijking op dezelfde
datums, invoerrijen, doelen en evaluatieregels worden getest.
