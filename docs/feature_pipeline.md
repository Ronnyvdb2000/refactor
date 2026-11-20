# Dagdata vóór scoring

## Uitvoering

1. **Generieke Technicals** start dagelijks om 22:15 UTC of handmatig, binnen de bestaande Yahoo-featurewachtrij.
2. De workflow bevriest de selectiepeildatum vóór de scan: vóór 22:15 UTC uiterlijk gisteren, vanaf 22:15 UTC vandaag. Dit is een conservatieve grens voor de gebruikte Europese/Noord-Amerikaanse dagdata, geen volledige beurskalender.
3. De builder behoudt het x-universum en voegt unieke tickers uit de recente selecties (peildatum en drie voorgaande kalenderdagen) toe. Daardoor krijgen selecties uit andere botuniversums eveneens een featurepoging. De doelkoersdag wordt niet vooruitgeschoven; de bestaande warm-up-, koers-, eindigheids-, recency- en dekkingscontroles blijven actief.
4. Alleen na een succesvolle bouwrun, met gecommitte features en de bestaande minimale bouwdekking, ontstaat `results/feature_build.json`. Een oud manifest wordt vóór het bouwen verwijderd; bij mislukking wordt geen succesmanifest gepubliceerd.
5. GitHub `workflow_run` start de XGBoost- en Ridge-meta-scoreworkflows na voltooiing. Alleen een succesvolle run uit `main` van dezelfde repository wordt toegelaten. Beide workflows checken de broncommit uit en downloaden het artifact met de exacte bronrun-ID.
6. Voor scoring worden status, schema, bronrun, commit, leeftijd en datumgrens van het manifest gecontroleerd. Nieuwere selecties wachten; toegelaten rijen gaan door dezelfde bestaande modelinvoercontrole. De nieuwste toegelaten selectiedatum met minder dan de vereiste dekking blijft een fout, ook wanneer oudere datums volledig zijn.

De losse 21:30-XGBoost- en 23:00-Ridge-scoringscrons zijn vervangen door deze opvolging. De zondagse Ridge-hertraining om 22:00 UTC blijft behouden en traint alleen; de dagbouw geeft scoring vrij. Automatische scoring kan nu elke succesvolle dagelijkse bouwrun volgen, ook in het weekend. De bestaande vrijdagregel voor weekendselecties blijft behouden. De fundamentals-workflow is geen afhankelijkheid toegevoegd: de huidige twee scorers gebruiken geen aparte fundamentals-join.

## Manifest en grenzen

Een herstart vervangt het artifact binnen dezelfde bronrun; de consumenten controleren ook de poging. Het manifest bevat schema, status, tijdstip, selectiegrens, bronrun-ID, uitvoeringspoging, commit en aantallen werkelijk opgeslagen features per koersdatum. Het bevat geen credentials. Consumenten weigeren een manifest ouder dan 30 uur, een toekomstige/onvoldoende afgesloten datum of een andere bronrun/poging/commit. Ook de bestaande maximale invoerleeftijd van de scorers blijft gelden. `BACKFILL_DATUM` voorkomt dat de builder na middernacht ongemerkt naar een nieuwe koersdag doorschuift.

Dit manifest is herkomst- en procesbewijs, geen onveranderlijke kopie van de database en geen garantie dat elke ticker een bruikbare koers heeft. De daadwerkelijke featuredekking wordt opnieuw op het opgehaalde modelcohort getoetst. Een late selectie of ontbrekende beursdag kan daarom nog terecht een scorepublicatie blokkeren. Er wordt niet geïmputeerd, niet naar een willekeurige oudere koersdag uitgeweken en geen datum van bestaande selecties of labels gewijzigd.

Handmatige scoreworkflows en CLI-aanroepen blijven beschikbaar zonder automatisch gekoppeld manifest; de bestaande strikte invoercontroles blijven daar actief. Een expliciet meegegeven manifest wordt ook dan gevalideerd. Een handmatige scorestart is dus geen vrijstelling van ontbrekende dagdata en geen bewijs dat de gekoppelde bouwketen is uitgevoerd.

## Modellen en rapportage

De bestaande getrainde XGBoost-modellen worden geladen; scoring hertraint niet bij iedere voorspelling. De Ridge-scorer behoudt zijn modelversie- en featurevolgordecontroles. Telegram vermeldt bij gekoppelde runs de bronrun, commit en peildatum, naast de bestaande selectie-/koersdatums en dekkingsrapportage. Er zijn geen tabellen, modelbestanden, featuresets, trainingssplits of kwaliteitsdrempels vervangen.

Dit past het algemene principe van gedeelde featurevoorbereiding en afzonderlijke modeltraining/inferentie toe, zoals beschreven in [Quantify's screener-pipeline](https://github.com/tushhh/quantify/blob/main/src/quantify/screener.py) en [trainingsworkflow](https://github.com/tushhh/quantify/blob/main/.github/workflows/ml_train.yml). De implementatie is zelfstandig geschreven; er is geen Quantify-code overgenomen. Het voegt geen CatBoost of ensemble toe.

## Controle

Offline regressies simuleren succes, gedeeltelijke bouw, ontbrekende/mismatchende/verouderde manifests, middernacht, nieuwe selecties buiten de bronpeildatum, selectie buiten x-lijsten en blokkering van onvolledige nieuwste datums. De bestaande GitHub-controleworkflow voert die regressies ook uit. De artifact-overdracht tussen twee echte productie-workflowruns moet bij de eerstvolgende dagbouw worden gecontroleerd; een offline of CI-test is geen claim van een reeds uitgevoerde productiescan.
