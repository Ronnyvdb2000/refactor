# V3/V4: kwaliteitsblokkade vóór modelpublicatie

De productietrainers `models.xgboostV3` en `models.xgboostV4` roepen
`common/model_publication.py` aan vóór hun bestaande atomaire opslag.
Een technisch laadbaar model is daarmee niet automatisch publiceerbaar.

## Drempels

De bestaande gedeelde kwaliteitstoets vereist:

- AUC minimaal 0,55; ontbrekende/ongeldige AUC geeft `HOLD`.
- Top-20%-rendement strikt boven de MA50-baseline op dezelfde testset.
- Minimaal 30 testrijen en vijf verschillende testdatums.
- Een berekenbaar relatief controlemodel; maximaal 0,10 AUC-voorsprong daarop.

De bestaande train/test-splitsing met horizonembargo blijft behouden. Bij V4
wordt de kenmerkdekking voortaan uitsluitend op de trainingsset bepaald.
De testset bepaalt niet welke technische of fundamentele kenmerken worden gekozen.

## Bestaand model vergelijken

Voor vervanging wordt het oude model opnieuw op exact dezelfde testrijen,
doeldefinitie en horizon beoordeeld. Het moet metadata bevatten die de
laatste trainingsdatum, doeldefinitie, modelversie en kenmerkvolgorde vastlegt.
De laatste trainingsdatum moet vóór de nieuwe testgrens minus horizonembargo
liggen. Ontbreekt die informatie, overlapt training mogelijk met de test,
ontbreken oude invoerkenmerken of faalt het laden, dan geldt `HOLD`.

Een kandidaat moet een **strikt hogere AUC** hebben dan het bestaande model,
zonder lager toprendement. Gelijke AUC is geen reden om te vervangen.
Alle aanwezige V4-productievarianten voor dezelfde horizon worden gecontroleerd,
zodat het wisselen tussen `tech` en `tech_fund` de blokkade niet omzeilt.
V3 en V4 worden niet rechtstreeks met elkaar vergeleken. De vergelijking gebruikt
aanwezige productie-PKL's in de werkmap. V4 bewaart modellen als artifacts, maar
herstelt oudere artifacts niet automatisch. Zonder aanwezig oud V4-PKL kan alleen
de absolute kwaliteitstoets worden toegepast; dat bewijst geen verbetering ten
opzichte van een eerdere V4-run.

Oudere V3-PKL's bevatten deze metadata niet. De nieuwe code verzint geen
trainingsherkomst voor die bestanden en vervangt ze daarom niet automatisch.
Een kandidaat blijft beschikbaar voor afzonderlijke beoordeling. Er is geen
nieuwe CLI-vlag toegevoegd om deze controle uit te schakelen.

Zonder bestaand productie-PKL voor een horizon kan een kandidaat die aan alle
absolute drempels voldoet als eerste model worden gepubliceerd. Nieuwe modellen
krijgen de metadata mee in het PKL-bestand. Bij een wijziging van een bestaand
bestand tussen beoordeling en opslag wordt publicatie afgebroken. De bestaande
workflowguards blijven nodig tegen gelijktijdige schrijvers.

## Uitvoer en status

`results/model_candidates/<versie>_<horizon>.pkl` bewaart de kandidaat, ook bij
`HOLD`. Opslaan gebeurt via een tijdelijk bestand, opnieuw laden, controle van
metadata en voorspellingen en vervolgens atomair vervangen. Het JSON-bestand
ernaast bevat het besluit, redenen, metrics, vergelijking en modelhashes.
Een fout tijdens kandidaatopslag blokkeert productiepublicatie.

De trainingsworkflows uploaden kandidaten en besluiten als artifact voor
90 dagen. Kandidaten staan buiten `models/`, zodat V3's bestaande Git-publicatie
ze niet als productiemodel commit. Bij `HOLD` blijft de productie-PKL byte voor
byte ongemoeid. De bestaande Telegram-/e-mailrapportage en `xgboost_runs`-logging
melden status `kwaliteit_hold` met de redenen. `getraind` betekent dat de
productieopslag daadwerkelijk is aangeroepen na een geslaagde controle.
Een correct berekende `HOLD` is geen technische fout; fouten bij laden van de
kandidaat of publicatie blijven wel fouten. V3 vereist nog steeds minstens één
bruikbaar productiemodel om zijn volledige run als geslaagd af te ronden.

De handmatige patchtool herkent de nieuwe productiegate en geeft die broncode
ongewijzigd door. Hij vervangt de strengere controle niet door zijn oudere
absolute drempelpatch. De afzonderlijke bestaande gecontroleerde trainers zijn
in deze wijziging niet opnieuw ontworpen.

## Grenzen

Dit zijn historische tests met overlappende handelsperioden. Vijf testdatums
zijn niet vijf onafhankelijke marktproeven. Het embargo gebruikt de bestaande
kalenderbenadering, geen exacte beurskalender. Testresultaten vergelijken bruto
rendementen; transactiekosten, portefeuillebeheer, kalibratie en toekomstige
voorspelkracht zijn hiermee niet bewezen. Herhaald kiezen op dezelfde testset
kan overfitting veroorzaken. De prospectieve V3-evaluatie blijft daarom nodig.
Metadata en modelhashes helpen controle, maar vormen geen extern onveranderbaar
bewijs van de trainingsdata.
