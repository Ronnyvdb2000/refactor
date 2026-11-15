# Weekly bronherstel en dekkingsbeleid — 15 november 2026

## Aanleiding

[Run 43682958379](https://github.com/Ronnyvdb2000/refactor/actions/runs/43682958379) op bfa9d11 verwerkte alle 14162 unieke symbolen in ongeveer vijftig minuten. De drie afgekeurde lijsten waren Benelux/Ierland (153/194), Helsinki (71/187) en Oostenrijk/Slovenië/Slowakije (36/96). Het rapport en de dekkings-CSV zijn opgeslagen; de nieuwe toppers-JSON is geblokkeerd. De koerscache is bewaard (5,34 MB).

De gebruiker bevestigde dat een afkeuring wegens onvoldoende brondata acceptabel is en dat tickerlijsten halfjaarlijks moeten worden nagekeken. De 80%-norm wordt daarom niet verlaagd en onvolledige markten worden niet stilzwijgend overgeslagen. Daarna is expliciet gevraagd ook de Helsinki-lijst recht te zetten.

## Correcties

Het herkomstregister `weekly/symbols.csv` bevat 33 aanvullende expliciete koppelingen: drie voor Brussel, drie voor Helsinki en 27 voor Wenen. Elke koppeling bewaart ISIN en primaire bron. De Wiener Börse-bedrijfskaarten zijn per ISIN opgehaald; de handelscode is rechtstreeks uit de kaart gelezen. Voorbeelden: AGRANA.VI → AGR.VI, BAWAG.VI → BG.VI, DOCO.VI → DOC.VI, FREQ.VI → FQT.VI en PORR.VI → POS.VI. Geen omzetting van Sloveense/Slowaakse tickers naar een andere beurs en geen keuze tussen onduidelijke aandelenklassen.

Een beperkte livecontrole leverde bruikbare weekdata voor 32 van de 33 vervangende codes, met basisdatum 6 november en einddatum 13 november 2026. MARI.VI heeft historie maar geen geldig weekrendement voor die week. De [Wiener Börse-melding](https://www.wienerborse.at/news/wiener-boerse-news/marinomed-handelsaussetzung-24092026/) vermeldt schorsing vanaf 24 september en verwijst naar schrapping op 6 november. Deze invoer is niet verwijderd om een hogere dekkingsscore te krijgen.

Combinatie van de oude volledige scan met de aanvullend gemeten koersen geeft voor de ongewijzigde Benelux/Ierland-lijst 156/194 (80,4%). Voor 059 geeft dit 61/95 (64,2%); de noemer daalt met één door de geverifieerde dubbele invoer RWTRACE.VI/RWT.VI. Dit zijn gecombineerde waarnemingen voor dezelfde week, geen nieuwe volledige productie-run. Lijst 059 blijft onvoldoende en kan de publicatie blokkeren.

## Helsinki opnieuw opgebouwd uit de officiële lijst

`tickers_054a.txt` bevat nu alle 198 noteringen uit de Nasdaq-screener voor markt HEL op de controledatum: 40 large cap, 54 mid cap, 56 small cap en 48 First North. Voor elk endpoint is gecontroleerd dat het aantal ontvangen rijen overeenkomt met het opgegeven totaal en dat één pagina alle rijen bevat. Alleen de categorie SHARES met EUR-notering is opgehaald. Geen selectie op koersbeschikbaarheid: ook bijvoorbeeld KESKOBN0126.HE blijft in de noemer staan.

De Nasdaq-symbolen worden letterlijk gebruikt met .HE; de expliciete Yahoo-schrijfwijze voor NDA FI is NDA-FI.HE. Het [volledige register](helsinki_universum_20261115.csv) bevat de officiële symbolen, namen, ISIN's, segmenten, bron-URL's en peildatum. De originele 478 invoerregels voor de drie onderzochte lijsten, waaronder de 188 oude Helsinki-regels, staan in [de audit](weekly_coverage_repair_20261115.csv). De oude Helsinki-invoer gaf 187 unieke codes; daarvan komen na de gecontroleerde codecorrecties 74 overeen met de nieuwe lijst. De volledige officiële lijst voegt 124 noteringen toe. Niet overeenkomen met een actuele lijst is geen zelfstandig bewijs van delisting of een vrijbrief voor een geraden alias.

De bronwijziging werkt door in alle bots die `tickers_054a.txt` inlezen. De x-kwaliteitsselectie is behouden. Dit is een actuele beurslijst voor nieuwe scans, geen historisch universum voor een rendementstest. De snapshot is gedateerd en kan bij de halfjaarlijkse broncontrole opnieuw worden gecontroleerd; hiervoor is geen automatische taak aangemaakt.

## Cache en verificatie

De a/x-workflows kunnen na een invoerwijziging terugvallen op de vorige koerscache van dezelfde week. De bestaande checkpointlezer controleert ieder bestand opnieuw op ticker en datumbereik. Nieuwe/veranderde codes halen hun eigen historie op. Daardoor hoeft een broncorrectie de eerder opgebouwde koerscache niet verloren te laten gaan. Telegram, 80%-norm en publicatieblokkering blijven actief.

De tests controleren de aanvullende beurscontext, behoud van onbekende invoer, geen verwisseling van tickers bij cachehergebruik en een volledig Nasdaq-register dat exact overeenkomt met de gedeelde Helsinki-bronlijst. YAML-cacheherstel, RUFF (F401,F821) en git diff --check worden vóór publicatie gecontroleerd.


## Live-eindresultaat Helsinki en offlinechecks

De definitieve lijst is lokaal volledig gecontroleerd voor de week 9–13 november 2026, zonder Telegram, e-mail, toppers-publicatie of database-export. De scan hervatte met 80 reeds gevalideerde koersreeksen en vroeg de overige 118 noteringen op. Sommige initiële batches meldden throttling; iedere retry herstelde zonder blijvende providerstop. Resultaat: **195/198 bruikbare weekrendementen (98,5%)**. De volledige [dekkingsmeting](weekly_helsinki_dekking_20261115.csv) houdt alle 198 officiële noteringen in de noemer, inclusief de drie ontbrekende/onvoldoende weekreeksen.

Dit bevestigt de nieuwe Helsinki-bronlijst en haar huidige dekkingsscore. Het is geen volledige nieuwe a-productierun en garandeert niet dat Yahoo een volgende run onbeperkt bedient. Lijst 059 blijft afzonderlijk onvoldoende.

`python -m pytest -q tests/`: **674 tests geslaagd**, 12 overgeslagen en 17 geslaagde subtests. RUFF (F401,F821), YAML-cacheherstel en `git diff --check` geslaagd. Er zijn geen echte notificaties of database-updates gedaan tijdens deze lokale controles.
