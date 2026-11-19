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
een afzonderlijk controlepunt (hersteld in de vervolgronde hieronder): actuele providerdata of een recente cacherij
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

## Vervolgronde: geen nieuwe terugdatering

Een alleen-lezen controle vond 18.276 fundamentals-rijen, waarvan 12.552 met
een `bijgewerkt_op`-UTC-datum later dan hun `datum`. De tijdstempelkolom is
`timestamp with time zone`; `datum` is tekst. Dit bewijst niet dat die waarden
op hun gelabelde datum beschikbaar waren. Het is geen vaststelling dat iedere
betrokken waarde inhoudelijk fout is; ook een latere legitieme wijziging kan de
vroegere beschikbaarheid oncontroleerbaar maken.

De builder gebruikt recente selecties nu uitsluitend om tickers voor de
ophaalwachtrij te kiezen. De bestemming is steeds de **UTC-ophaaldatum**,
eenmaal per ticker. Er worden geen actuele providerwaarden meer onder eerdere
selectiedatums gezet. Selecties ná de ophaaldatum worden niet gebruikt voor de
wachtrij. Alleen reeds aanwezige snapshots van dezelfde dag voorkomen een nieuwe
aanvraag; de oude driedaagse cache wordt niet naar andere datums gekopieerd.

De opslagfunctie weigert andere datums dan de huidige UTC-datum. De INSERT
gebruikt `ON CONFLICT DO NOTHING`: herhaalruns behouden de eerste dagsnapshot
en verversen ook de tijdstempel niet. De geretourneerde schrijftelling gebruikt
werkelijke ingevoegde rijen. Een gelijktijdige run die dezelfde snapshot eerder
opslaat, veroorzaakt daardoor geen overschrijving of dubbele schrijftelling.
Geen schemawijziging of historische herstelactie nodig.

De drie lezers van deze tabel (`meta_models/a_par_model.py`,
`analyse_parameter_ic.py` en `xgboostV4.py`) gebruiken dezelfde beschikbaarheidsregel:
`bijgewerkt_op` moet bekend zijn en zijn UTC-datum mag niet ná `datum` liggen.
De bestaande LEFT JOINs blijven behouden: ontbrekende betrouwbare fundamentals
verwijderen technische invoerrijen niet stilzwijgend. De bestaande modellen
blijven hun eigen beleid voor ontbrekende factoren toepassen. Dit kan minder
fundamentele factoren, andere IC-gewichten of onvoldoende modelinvoer opleveren.
Er zijn geen historische rijen verwijderd of gewijzigd en geen modellen hertraind.

Deze controle geldt op **dagniveau**. Zij bewijst niet dat data vóór een intraday-
selectie of vóór beurssluiting bekend was; daarvoor ontbreken bewaarde exacte
selectie-/waarnemingstijden en bronpublicatietijden. Oudere onbekende versies
kunnen niet uit een latere update worden gereconstrueerd. Historische rijen
zijn dus niet achteraf volledig betrouwbaar verklaard.

### Verificatie van de vervolgronde

Acht aanvullende offline regressies controleren de ophaalwachtrij, cachegrens,
geweigerde opslagdatums, de daadwerkelijk uitgevoerde conflict-INSERT in SQLite,
behoud van de eerste snapshot en de beschikbaarheidsregel in alle drie lezers.
De volledige suite slaagt met **806 tests en 19 deeltests**, dertien optionele
tests overgeslagen, met uitgaande socketverbindingen geblokkeerd.

Alle vier gegenereerde leesqueries zijn als `SELECT count(*) FROM (...)` op de
bestaande PostgreSQL-database uitgevoerd: parametermodel 48.811 invoerrijen,
parameter-IC 48.811, XGBoostV4 26.279 en 247 tickers zonder dagsnapshot in de
ophaalwachtrij. De eerste drie zijn totale join-aantallen, geen aantallen complete
fundamentele modelrijen. Er zijn geen opslagprogramma's of notificaties live gestart.
