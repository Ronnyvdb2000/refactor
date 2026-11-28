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
