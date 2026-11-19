# Generieke fundamentals: runstatus en Telegram — 19 november 2026

Uitgangspunt `main` op `a2dfea0`, na de verplaatsing van de basisbots naar strats.
Scope: `bouw_generieke_fundamentals.py` en `run_generieke_fundamentals.yml`.

## Concrete fouten

De workflow gaf Telegram-secrets door maar het script verstuurde geen melding.
De builder kon na uitgeputte rate-limitpauzes normaal eindigen en dus een groene
workflow opleveren, ook wanneer de geplande verwerking was afgebroken. Ook een
volledig mislukte ophaalronde had geen foutstatus. Rate limits die yfinance alleen
logde of die bij optionele cashflow/resultaat/balansvragen werden opgevangen,
konden als ontbrekende data worden behandeld.

## Hersteld

- Een bekende Yahoo-rate-limit stopt direct verdere tickeraanvragen. Geen extra
  pauzes of herstartlus. De eerdere `RL_DREMPEL`/`RL_PAUZE_SEC`/`RL_MAX_PAUZES`
  zijn verwijderd; de workflow stelde deze niet in. Zowel expliciete als gelogde
  429-meldingen worden bewaakt, inclusief optionele fundamentele bronvragen.
- Een providerstop, overige verwerkingsfouten of een ophaalronde zonder één
  bruikbaar resultaat geeft een foutstatus. Eerder gecommitteerde rijen blijven
  bewaard; een mislukte nieuwe aanvraag wordt niet als bruikbare rij opgeslagen.
- De dagelijkse cap van 200 downloads blijft actief. Werk buiten deze geplande
  cap is op zichzelf geen fout; de melding toont hoeveel tickers niet zijn
  opgevraagd. Een cache-only run of niets-te-doen-run blijft eveneens geldig.
- Telegram rapporteert succes, niets te doen of ONVOLLEDIG in platte tekst.
  De oorspronkelijke scanfout blijft bestaan als verzending mislukt; er volgt
  een zichtbare logwaarschuwing. Onverwachte exceptionteksten met mogelijke
  verbindingsdetails worden niet naar Telegram doorgestuurd.
- De verbinding sluit ook bij fouten; ongebufferde workflowlogging is toegevoegd.

Geen wijziging van de fundamentele rekenregels, opslagkolommen of queryvensters.
Het bestaande kopiëren van recente fundamentals naar ontbrekende ticker/datumpaar-
rijen is hiermee niet als causaal historisch betrouwbaar gevalideerd. Dit blijft
een afzonderlijk controlepunt: actuele providerdata of een recente cacherij
bewijst niet dat die waarden op een eerdere selectiedatum bekend waren. Bestaande
historische rijen zijn niet aangepast of verwijderd.

## Verificatie

Veertien gerichte offline regressies controleren de runlimiet, bekende en gelogde
rate limits, ontbrekende data, fouten na deels geslaagde opslag, databasefouten,
verzendfouten, niets te doen, cache-only verwerking en 429's uit info, cashflow,
resultaten en balans. Database, provider en Telegram zijn gemockt.

De volledige suite slaagt met **798 tests en 19 deeltests**, dertien optionele
tests overgeslagen. Uitgaande socketverbindingen waren tijdens de tests geblokkeerd.
Geen live scan, productiedatabasewrite of testbericht gestart. Workflowaudit:
65 workflows zonder fouten of waarschuwingen. Het bronbestand behoudt CRLF;
de whitespacecontrole gebruikt daarom `git -c core.whitespace=cr-at-eol diff --check`.
TradingAgents, refactor-lab en lokale wijzigingen uit andere gesprekken blijven apart.
