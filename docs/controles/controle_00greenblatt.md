# Controle Greenblatt — 13 november 2026

Repository: Ronnyvdb2000/refactor. Alleen Greenblatt en zijn workflow zijn aangepast;
gedeelde helpers en de Telegram-instellingen van andere bots blijven ongewijzigd.

## Hersteld

- Koersen tonen de Yahoo-koersvaluta, inclusief GBp voor pence; geen vaste EUR-labels.
- De minimum-marktkapitalisatie van 50 miljoen wordt nu consequent in USD getoetst.
  Niet-USD marktkapitalisaties worden met een recente, binnen de run gecachte
  Yahoo-FX-slotkoers omgerekend. Ontbrekende of meer dan zeven dagen oude FX-data
  geeft een zichtbare uitsluiting. Voor GBp/GBX, ZAc en ILA worden totale
  marktkapitalisatie en EV in de bijbehorende hoofdvaluta gelezen; alleen de
  per-aandeelkoers draagt het subunitlabel.
- Verschillende koers-/verslagvaluta worden expliciet overgeslagen. De bot doet
  geen ongedocumenteerde conversie van een mogelijk anders uitgedrukte EV.
- EBIT en balans gebruiken hetzelfde laatste gemeenschappelijke boekjaar. Een
  toekomstig boekjaar of een jaarafsluiting ouder dan 550 dagen wordt uitgesloten.
  Eindige waarden zijn verplicht; Operating Income kan voor hetzelfde boekjaar
  dienen als EBIT ontbreekt.
- Net Tangible Assets wordt niet meer als Net PPE gebruikt: het zijn verschillende
  balansbegrippen. Ontbrekende schuld of cash wordt bij een EV-fallback niet als
  nul ingevuld. Werkelijke nulwaarden blijven geldig.
- ROC en earnings yield blijven op volle precisie beschikbaar voor de ranking.
  Gelijke waarden krijgen gelijke competition-rangen (1, 1, 3). De eindvolgorde is
  onafhankelijk van de volgorde van de tickerlijsten.
- Overlap tussen beursbestanden wordt per beurs correct geteld; downloaden,
  ranking en opslag blijven één keer per unieke ticker plaatsvinden.
- Het e-mailrapport bevat datadekking, redenen voor uitsluiting, ontbrekende
  tickerlijsten en het werkelijk aantal geslaagde database-inserts.
- Een e-mailfout, onverwachte analysefout, databasefout of een volledig door
  dataproblemen mislukte scan geeft een foutstatus. Een geldige lege selectie
  door uitsluitend de selectiecriteria wordt als zodanig gerapporteerd.
- Telegram blijft in de workflow ongeconfigureerd en wordt dan niet aangeroepen.
  PYTHONUNBUFFERED maakt voortgangslogs direct zichtbaar.

## Reikwijdte van de uitkomst

Dit blijft een wereldwijde Yahoo-gebaseerde benadering, geen reproductie van de
officiële screener. De bestaande kapitaalformule blijft **Current Assets - Current
Liabilities + Net PPE**. Er is geen aanvullende correctie voor overtollige cash
of rentedragende kortlopende schuld ingebouwd. De teller is jaarlijks EBIT
(met Operating Income als beschikbare vervanging), geen gegarandeerde TTM-EBIT;
de ondernemingswaarde is actueel. Het rapport noemt deze keuzes expliciet.

De [officiële FAQ](https://www.magicformulainvesting.com/Home/Faqs) beschrijft de
sectoruitsluitingen, actuele fundamentele screening en het Amerikaanse bereik
van de officiële dienst. Deze repository behoudt zijn eigen wereldwijde universum.

## Validatie

24 offline regressietests geslaagd:

```bash
python -m unittest discover -s tests -p 'test_00greenblatt.py' -v
```

Getest: bekende ratio's, ontbrekende en oneindige waarden, valutalabels en
USD-drempel, boekjaarafstemming, ongeldige PPE-vervanging, EV-fallback, FX-cache,
gelijke rangen, overlap tussen beurzen, lege selectie en verzend-/opslagfouten.
Yahoo, SMTP, Telegram en database zijn gemockt; er zijn geen live-notificaties
verzonden. Een nieuwe workflow-run op main blijft nodig voor live-validatie.
