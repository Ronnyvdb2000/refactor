# Centrale onderzoekstabel sel_par

`public.sel_par` bevat één rij per aandeel en selectiedatum. `strategieen`
bewaart alle selecterende strategieën. De dagelijkse workflow start om
04:45 UTC (05:45 Belgische wintertijd, 06:45 zomertijd). GitHub en de gedeelde
Yahoo-wachtrij kunnen de werkelijke start uitstellen.

## Directe keten

1. De strategiebots schrijven hun oorspronkelijke signalen naar `selecties`.
2. `public.zaai_sel_par` zet de selecties tot en met de vorige UTC-dag over.
   Gelijke waarden worden samengevoegd; tegenstrijdige waarden worden gemarkeerd.
3. `maintenance.sel_par_direct` vult ontbrekende technische kenmerken en
   fundamentele waarnemingen rechtstreeks in `sel_par` aan.
4. Dezelfde module vult ontbrekende vervolgkoersen en rendementen voor
   5/10/20/30/60 waargenomen beurssessies aan, inclusief de werkelijke koersdatums.

De nieuwe keten leest geen `generieke_technicals`, `generieke_fundamentals` of
`forward_returns`. De rekencode van de bestaande indicator- en fundamentalbuilders
wordt hergebruikt zonder hun databasebouwers uit te voeren. Eén koersdownload
per benodigd aandeel wordt voor technische kenmerken en koersopvolging hergebruikt;
één fundamentele opvraag wordt gedeeld tussen de ontbrekende selectierijen van dat aandeel.
TradingAgents Bridge maakt geen deel uit van de keten.

## Modelinvoer uit één tabel

Alle bestaande modellen, meta-classifiers en analyses die de drie featuretabellen
lazen, gebruiken nu `common.sel_par_inputs.sel_par_query`. De SQL-projecties geven
hun bestaande kolomnamen terug, maar iedere fysieke gegevensscan leest uitsluitend
`public.sel_par`. Er is geen fallback naar de oude featuretabellen of `selecties`.
De bronstrategieën en hun oorspronkelijke parameters staan als snapshots in
`strategie_details` binnen dezelfde centrale rij. De dagelijkse seedstap bewaart
ze met gewone rij-updates; de databasefunctie en toegangsrechten blijven ongewijzigd.

Alleen rijen met `verrijking_status.invoer_vrijgegeven=true` zijn zichtbaar voor
modelinvoer. Nieuwe rijen worden pas na afgeronde verrijking en manifestcontrole
vrijgegeven. Bestaande technische snapshots worden op hun echte koersdatum
ontdubbeld; weekendscoring behoudt de expliciete vrijdagregel. Fundamentals die
na de selectiedatum zijn opgehaald blijven uitgesloten van historische invoer.

De drie oude builderworkflows hebben geen cron meer. Hun handmatige en CLI-ingang
meldt dat de centrale keten ze vervangt. De tabellen zelf blijven bewaard als archief.
Rekenfuncties en historische onderhoudsfuncties blijven beschikbaar voor tests;
ze worden niet meer als productiebuilders uitgevoerd. Technische backfill leest
en schrijft voortaan de `tech_`-kolommen in `sel_par`.

De twee dagelijkse scorers volgen een geslaagde `sel_par`-workflow en downloaden
het manifest van exact die bronrun. Hun bestaande score- en modeluitvoertabellen
blijven behouden. Losstaande trainings- en analyseworkflows gebruiken eveneens
de centrale invoer, maar behouden hun bestaande kalenderplanning.

## Planning en herstarts

De workflow controleert eerst of de geplande strategieruns van de vorige
selectiedag zijn gestart en voltooid. Oude featurebuilders zijn geen inhoudelijke
afhankelijkheid. De Yahoo-aanvragen delen wel de bestaande `features`-wachtrij.
Ontbrekende of nog actieve strategieruns blokkeren de overzetting; er wordt maximaal
twee uur gewacht. Afgeronde mislukte scans staan expliciet in het rapport;
hun eventueel opgeslagen gedeeltelijke selecties worden wel meegenomen.

De selectieoverzetting gebeurt atomair en eenmaal per selectiedag. Bestaande
verrijking wordt niet door lege seedvelden overschreven. De oorspronkelijke
strategiebron en dubbele bronselecties blijven behouden. Verandert de instapkoers,
dan worden de gekoppelde labels ongeldig gemaakt. Floatkoersen worden zonder
JSON-omzetting overgenomen, zodat hun oorspronkelijke precisie behouden blijft.

De verrijking bewaart voortgang per aandeel. Begrenzing: 2000 aandelen en 150 minuten
per uitvoering. Providerstops, fouten en resterend werk maken de uitvoering zichtbaar
onvolledig. Een herstart vult verder aan; bestaande labels worden niet herberekend.
`sel_par_runs` en het workflowartifact bevatten de overzetting en verrijkingsstatus.
`force=true` herhaalt zo nodig ook de selectieoverzetting.

## Datums en historische betrouwbaarheid

- Technische kenmerken gebruiken alleen afgesloten historische bars op of vóór de
  selectiedatum, met voldoende opwarmhistorie. `tech_koersdatum` bewaart de echte bar;
  bij weekendselecties kan dat de voorgaande beursdag zijn (maximaal zeven dagen oud).
- Nieuwe fundamentals behouden de echte ophaaldatum in `fund_observatiedatum`.
  Een actuele waarneming bij een oudere selectie krijgt
  `fundamentals_tijdig_bekend=false`: niet als historische modelinvoer gebruiken.
  De keten verzint geen historische fundamentals en overschrijft bestaande snapshots niet.
- Vervolgkoersen tellen beschikbare waargenomen sessies vanaf de laatste koersbar
  op of vóór selectie. Ze zijn geen onafhankelijke controle op een formele beurskalender.
  Ontbrekende dagen en verdachte eenheids- of splitverschillen kunnen beoordeling vereisen.
- De splitgecorrigeerde rendementsbasis staat in `rendement_basis_koers`; de originele
  selectiekoers blijft intact. Een latere wijziging van die basis blokkeert het mengen
  van oude en nieuwe labels. Oorspronkelijk gekopieerde labels hebben niet automatisch
  een geverifieerde basis of vervolgkoersdatum.
- Toekomstige koersen, rendementen, modeluitkomsten en bronmetadata zijn geen
  automatische invoerkenmerken. De historische beschikbaarheid van alle oorspronkelijke
  selectieparameters is door deze overzetting niet bewezen.

## Installatie

De bestaande tabellen `sel_par`, `selecties_onderzoek_strategie_bron` en
`sel_par_runs` moeten aanwezig zijn. `maintenance/sql/20261122_sel_par_direct.sql`
installeert de seedfunctie en de extra herkomstkolommen. De eerdere kopieerfunctie
`ververs_sel_par` wordt niet meer door de workflow gebruikt. De productieomgeving
`fantasie` is gemigreerd; de SQL in git is de reproduceerbare definitie.
