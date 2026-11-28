# Modelketen: gereedheid, tijdgrenzen en bewijs

De modelworkflows in `pipeline_routes.json` vereisen een geldig manifest van
`refresh_sel_par.yml`, ook bij handmatige en geplande uitvoering. Automatische
consumenten blijven gekoppeld aan de exacte bronrun, poging en commit. Andere
uitvoeringen kiezen de laatst afgeronde featurebouw op vertrouwde `main`.
Een mislukte, verlopen of ambigue laatste bouw leidt tot stoppen vóór het model;
er wordt geen oudere geslaagde bouw gezocht om de controle te omzeilen.
De bestaande versheids- en dekkingscontroles blijven gelden.

De logische `sel_par`-projecties begrenzen alle gelezen selectiedata tot de
bevestigde cutoff. Een manifest bewijst de succesvolle bouw en herkomst, niet
dat alle databasewaarden daarna onveranderlijk zijn. Nieuwe kandidaatmetadata
bevat daarom tevens een hash van de daadwerkelijk gebruikte trainingsinvoer.
Historische reconstructies gebruiken hun reeds vastgelegde bron, koersen en
werkelijke labeldatums; zij kunnen geen live scoring autoriseren.

## Werkelijke label-einddatums

`common.temporal_contract` leest `label_end_5d/10d/20d/30d/60d` uit
`sel_par.koersdatums`. Ontbrekende/ongeldige datums, een einddatum vóór of op de
selectiedatum en labels met een reviewmarkering autoriseren geen training.
Iedere trainingsuitkomst moet strikt vóór de test-/beslissingsdatum bekend zijn,
en uiterlijk op de bevestigde featurecutoff eindigen. Dit geldt ook voor de
interne LightGBM-validatiesplit, Ridge, classifiers en parametergewichten.

`calendar_embargo` is één gedeelde aanvullende kalenderbuffer. Het is geen
beurskalender en geen vervanging voor deze einddatumcontrole. Uitschakelen van
een aanvullende buffer schakelt de werkelijke labelgrens niet uit. Voor excess-
targets wordt de trainingsmediaan opnieuw berekend na het verwijderen van nog
onbekende uitkomsten: anders zou hun rendement de overige targets beïnvloeden.

## Overlappende testuitkomsten

Kandidaatrapporten registreren naast rijen en selectiedatums het aantal
niet-overlappende vensters. Per selectiedatum geldt het laatst eindigende geldige
label; vervolgens worden chronologisch vensters gekozen waarvan de start
strikt na het laatst gekozen einde ligt. Dit is een conservatieve beschrijvende
telling, geen bewijs van statistische onafhankelijkheid of een gecorrigeerd
betrouwbaarheidsinterval.

XGBoost-publicatie en gecontroleerde LightGBM/CatBoost-kandidaten vereisen
minstens drie zulke vensters en geen onbekende testintervallen. Deze minimumgrens
is een operationele ondergrens, geen bewijs van rendement. Bestaande modellen
zonder aantoonbare werkelijke laatste trainingslabeldatum blokkeren automatische
vervanging; zij worden niet achteraf van verzonnen herkomst voorzien.

## Bestaande resultaten en paperproef

Vastgelegde historische resultaten worden niet met nieuwe regels overschreven.
De implementatiehash omvat nu ook de gedeelde horizon- en labelcontroles;
een nieuwe reconstructie vraagt dus een nieuwe bronrun.

Bij uitsluitend een wijziging van de trainingsimplementatie blijft de bestaande
paperproef haar vastgelegde mandjes volgen met het oorspronkelijke protocol.
Zij opent geen nieuwe mandjes onder gewijzigde trainingsregels. Een afzonderlijke
proefversie is nodig voor nieuwe beslissingen. Een gewijzigde uitvoeringsengine,
kostenaanname of andere protocolinstelling blijft een harde blokkering.

Handmatige productiescoring gebruikt dezelfde dagregistratie en locks als de
automatische route. Een afgeronde model/cutoff wordt niet dubbel gescoord;
een onzekere eerdere poging wordt niet automatisch herhaald. Daarmee worden
ook dubbele Telegram-/mailmeldingen bij een herstart beperkt.

## Centrale Supabase-vrijgave (manifest v2)

De bestaande rij in `public.sel_par_runs`, met sleutel `selecties_tot`, bevat nu
`resultaat.data_ready`, `resultaat.run_manifest` en de canonieke SHA-256
`resultaat.manifest_hash`. Er is geen nieuwe tabel, functie of publiek API-recht.
Het GitHub-artifact moet exact overeenkomen met dit centrale manifest: bronrun,
poging, commit, cutoff, dekking en onderdelen. Een oude `input_released=true`
zonder dit bewijs is geen geldige vrijgave.

De producer houdt de exclusieve invoerlock vast, verifieert de vereiste bron-
scans, afgeronde verrijking en werkelijke dekking, en maakt het manifest v2.
Ontbrekende, actieve of mislukte vereiste scans blokkeren de vrijgave; dit zijn
geen individuele ontbrekende aandelen. De rijvrijgave en `data_ready=true`
worden als laatste in dezelfde database-transactie geschreven. Een nieuwe
bouw trekt de eerdere vrijgave in voordat zij de invoer vervangt.

Per selectiedag worden unieke verwachte, geldige, uitgesloten en nog wachtende
tickers geregistreerd. De verwachte populatie komt rechtstreeks uit de bron-
selecties, niet alleen uit de reeds overgezette `sel_par`-rijen. Een niet over-
gezette selectie blijft wachtend en mag niet als ontbrekende koers verdwijnen. Een onvoltooide controle mag niet als uitgesloten ticker
worden geteld. Geen wachtende tickers zijn toegestaan. De nieuwste cohort moet
minstens 80% geldige tickers hebben; de bestaande globale 80%-controle blijft
ook behouden. Een eindig positief instapkoers en correct gedateerde volledige
technicals zijn vereist voor een geldige ticker. Bevestigde ontbrekende koersen
mogen uitgesloten worden. De bestaande portefeuillelogica bepaalt de cash
bij overgeslagen posities.

De nieuwste selectiecohort moet bij de cutoff horen volgens de bestaande
weekendregel: op werkdagen dezelfde selectiedag, in het weekend geen cohort
ouder dan vrijdag. Ontbrekende dagen worden niet stilzwijgend opgevuld met een
oudere cohort. Historische labels die nog niet rijp zijn worden niet als
ontbrekende productie-invoer beschouwd: de afzonderlijke labelgrens bepaalt
welke uitkomsten mogen meetellen bij training.

De resolver controleert het artifact tegen Supabase. `maintenance.run_ready`
controleert dit opnieuw vlak vóór elke trainings-/evaluatiemodule en houdt de
gedeelde invoerlock tijdens het hele subprocess vast. Productiescoring doet
dezelfde controle binnen `score_once`, naast haar eigen model- en dagregistratie.
Een gewijzigde of ingetrokken vrijgave tussen resolver en uitvoering blokkeert
dus alsnog. Alle 16 routes staan in `pipeline_routes.json`.

Invoering vereist een nieuwe succesvolle centrale bouw. Oude dagregistraties
worden niet achteraf als compleet gemarkeerd. Bij herhaling van een bestaande
v2-vrijgave worden dekking en herkomst opnieuw gecontroleerd; onzekere eerdere
scoringspogingen worden nog steeds niet automatisch herhaald.
