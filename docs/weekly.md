# Weekly bots

Alle zes weekly scripts staan in `weekly/`. Start vanuit de repository-root als module; tickerbestanden en JSON-uitvoer blijven in de hoofdmap. `weekly/shared.py` bevat alleen weekgrenzen, rapportage en koerscache voor deze groep.

| Script | Workflow | Functie |
| --- | --- | --- |
| `weekly/weekly_report.py` | `weekly_run.yml` | Rapport voor 01–09 en 041a–059a; `laatste_toppers.json` |
| `weekly/weekly_report_x.py` | `weekly_run_x.yml` | Rapport voor 01–09 en 041x–059x; `laatste_toppers_x.json` |
| `weekly/weekly_report_opvolg.py` | `weekly_run_opvolg.yml` | Weekselecties en historische dagparameters naar Supabase |
| `weekly/weekly_db_opvolg.py` | Via opvolg en correlaties | Opslag en ranks op peildatum; geen zelfstandige workflow |
| `weekly/weekly_rep_backtest.py` | `weekly_runV2.yml` | Retrospectieve technische verkenning van weekwinnaars |
| `weekly/analyseer_weekly_correlaties.py` | `analyse_correlaties_weeklytop.yml` | Handmatige beschrijvende correlaties; CSV en e-mail |

Voorbeeld: `python -m weekly.weekly_report`. Dit start de echte scan met notificaties. Alleen `python -m weekly.weekly_rep_backtest --help` toont hulp zonder uitvoering.

## Weekgrenzen en rendement

De a-rapportage en opvolg starten vrijdag 23:00 UTC; de x-rapportage om 23:15 UTC. In België is dat zaterdag 00:00 in de winter en 01:00 in de zomer. Een handmatige run gebruikt de laatste volledige week; vóór vrijdag 23:00 UTC geldt nog de vorige week.

Weekprestatie loopt van de laatste beschikbare slotkoers vóór maandag tot het laatste beschikbare slot van de week, met aangepaste koersen. Vijf slotkoersen van maandag tot vrijdag zouden alleen vier dagrendementen meten. De basis mag maximaal zeven kalenderdagen vóór maandag liggen; de eindkoers maximaal drie dagen vóór vrijdag. Ontbrekende of oudere koersen worden uitgesloten en aantallen verschijnen per lijst. Positieve stijgers en negatieve dalers worden apart gerangschikt; de grootste daler krijgt rang 1. De neutrale steekproef blijft per lijst/week reproduceerbaar.

Elke unieke ticker wordt per proces gecachet. Downloads gebruiken batches van maximaal 20, twee workers en een timeout van 15 seconden. Ontbrekende data krijgt maximaal één batchretry; er volgen geen individuele retries. Bij een rate-limit volgt één herstelpauze van 15 seconden en bij herhaling stopt de scan. Vóór elke batch zit twee seconden pauze, ook tussen opeenvolgende laadacties. De opvolgbot scant het gehele universum met korte historie en haalt lange historie alleen voor geselecteerde tickers op. Verschillende runners kunnen samen nog steeds providerlimieten raken.

## Hervatten na een providerstop

De a/x-rapportworkflows zetten `WEEKLY_CHECKPOINT_DIR=.weekly-cache` en zoeken eerst de laatst bewaarde voortgang voor dezelfde week en dezelfde invoer/code. Als die sleutel ontbreekt, kan de laatst bewaarde cache van dezelfde week worden gebruikt. Elk bestand wordt alsnog op ticker en periode gevalideerd; gewijzigde of nieuwe tickers worden afzonderlijk opgehaald. Zo maakt een broncorrectie niet alle bestaande weekkoersen onbruikbaar. Na elke volledig afgehandelde batch worden alleen de reeksen met een bruikbaar weekrendement atomair als CSV bewaard. Een volgende uitvoering downloadt die reeksen niet opnieuw. Ontbrekende en verouderde weekdata worden opnieuw geprobeerd; een onderbroken batch telt niet als afgerond. De hervatcache is optioneel en staat los van de gepubliceerde toppers-JSON.

De cache is gescheiden per historieperiode en eindweek, met controle op ticker, koerskolommen en datumbereik. Onleesbare bestanden worden opnieuw opgehaald. De workflows bewaren de cache ook na een providerstop. De scanstap heeft een limiet van 75 minuten binnen het jobbudget van 90 minuten, zodat er ruimte blijft voor cache- en artifactopslag. Bij een harde runneruitval kan de laatste voortgang alsnog verloren gaan; Actions-cache kan bovendien ontbreken of verwijderd zijn. Een nieuwe week begint met nieuwe koersdata.

Dit verlaagt de verzoekdruk en voorkomt bij een gewone providerstop herhaald downloaden van eerder gevalideerde data. Het garandeert geen Yahoo-toegang en verandert de 80%-controle niet. Een mislukte run blijft rood en publiceert geen nieuwe toppers. Er is geen automatische herstartlus.

## Telegram, e-mail en workflow

Telegram blijft behouden voor de weekly rapporten, opvolg, winnaarsanalyse en correlatie-analyse. Secrets blijven in de YAML staan. Rapportberichten gebruiken tekst zonder Markdown-parsing; lange berichten worden opgedeeld. Verzendfouten worden gelogd. De a/x-rapporten leveren daarnaast tekst-artifacts en e-mail wanneer de e-mailsecrets beschikbaar zijn. De bestaande e-mail van correlaties blijft behouden. Bij volgende botcontroles wordt Telegram behouden volgens de gebruikersinstructie van 14 november 2026.

Beide JSON-schrijvende workflows delen een concurrency-groep en bewaren uitvoer alleen na succes op main. Git gebruikt rebase en gewone pushes; een conflict wordt zichtbaar in plaats van met `-X ours` overschreven. De backtest volgt via `workflow_run` op een geslaagd Weekly Report en blijft handmatig startbaar. De output van de oude JSON-lijsten zonder weekdatum wordt geweigerd; draai eerst de nieuwe Weekly Report. Iedere nieuwe topper bevat weekgrenzen, werkelijke koersdatums en `data_version=3`.

## Historische betrouwbaarheid

De opvolg bewaart technische parameters alleen op handelsdagen binnen de gekozen week. Ranks komen van dezelfde beurs, zijn maximaal 30 dagen oud en moeten op de peildatum al opgeslagen zijn geweest. Ontbrekende ranks blijven NULL. Actuele Yahoo-fundamentals en actuele short/float-data worden niet naar historische dagen gekopieerd. Ook Debt/Equity wordt niet meer onder de naam Debt/EBITDA opgeslagen.

Een herhaalrun behoudt de eerste selectie per ticker/beurs/week; een ander type veroorzaakt geen extra rij. Parameter-upserts vullen alleen NULL aan. Databasefouten worden doorgegeven, zodat een mislukte export geen groen succesrapport oplevert. Bestaande gegevens zijn tijdens deze refactor niet aangepast of verwijderd.

De correlatie-analyse gebruikt alleen weken vanaf 16 november 2026. Oudere waarden met de eerdere definities blijven bewaard en zijn uitgesloten. De analysetarget is het rendement tussen de eerste en laatste werkelijk opgeslagen dagslotkoersen binnen de week, dus niet het gehele vorige-vrijdag-tot-vrijdag rapport-rendement. Dubbels worden in de analyse uitgesloten en actuele fundamentals worden niet als historische verklarende variabelen getest. De steekproef is achteraf geselecteerd en ticker-weken zijn afhankelijk: p-waarden/FDR blijven verkennend en bewijzen geen voorspelkracht.

De zogenoemde backtest blijft een retrospectieve winnaarsanalyse. De piekdag wordt achteraf gekozen; dit is geen onafhankelijke rendementstest van een verhandelbare strategie. Koersdata stopt bij de opgeslagen weekgrens; indicatoren stoppen vóór de gevonden piekdag. De eerste dag van het zoekvenster telt nu mee. Short/float blijven historisch onbekend.

## Live controle van 14 november: onvolledige brondata

De eerste grote run na de verplaatsing duurde 36 minuten en 51 seconden en publiceerde ondanks veel providerfouten een JSON-lijst. De kleine eerdere test had deze schaalproblemen niet blootgelegd. Frankfurt leverde 165/1418 bruikbare tickers; Warschau 26/361 en Helsinki 67/188. Een groene YAML betekende hier alleen dat het proces voltooid was.

De weekly invoer normaliseert nu kale symbolen uit lijst 043 naar .DE en Ierse .I-symbolen uit 041 naar .IR. De bronbestanden zelf blijven behouden. Symbolen uit andere lijsten worden niet op basis van een gok hernoemd. Daardoor is een Amerikaanse AMD geen Frankfurt-koers meer. Lijst 055 wordt als CSE aangeduid.

Een niet-lege lijst moet minstens 80% bruikbare weekrendementen hebben; bij kleine lijsten is maximaal één ontbrekende ticker toegestaan. Lege kwaliteitslijsten worden overgeslagen. Bij minder data komt geen ranglijst voor die lijst en krijgt het gehele rapport de status ONVOLLEDIG met een rode workflow. Er wordt dan geen nieuwe toppers-JSON gepubliceerd, de automatische winnaarsanalyse start niet en opvolg schrijft geen selecties naar de database. De Telegram-melding blijft aan en toont de beperking. Ook bij geslaagde lijsten staat het aantal bruikbare tickers in het rapport en de JSON; 80% is een minimum voor indicatieve ranglijsten, geen garantie van volledige dekking.

Het artifact weekly_dekking_<variant>.csv bevat per lijst en ticker of een bruikbaar weekrendement beschikbaar was. Bij een providerstop blijft de al gemeten dekking behouden. De kolom `status` onderscheidt `bruikbaar`, `geen_bruikbare_weekdata`, `download_afgebroken` en `niet_opgevraagd`. Voor de laatste twee blijft `bruikbaar` leeg: niet onderzocht betekent niet bewezen onbruikbaar. De diagnose gebruikt uitsluitend de bestaande cache en start geen extra downloads. Dit geldt ook voor opvolg. De oude versie-2-JSON is niet meer geldig voor de winnaarsanalyse; die vereist een nieuwe versie-3-snapshot.

## Onderzoek Warschau en Helsinki — 15 november

De weekly invoer gebruikt daarnaast expliciete beurscodes uit `weekly/symbols.csv`. Elke koppeling bevat de oorspronkelijke naam, handelscode, ISIN, bron-URL en controledatum. Voor Warschau zijn de naam en handelscode op dezelfde officiële GPW-bedrijfskaart gecontroleerd, met het ISIN uit de actuele bedrijvenlijst. Voor Helsinki staan alleen afzonderlijk geverifieerde koppelingen in het register. De normalisatie geldt uitsluitend voor de a/x-lijsten van de betreffende beurs. De gemeenschappelijke bronbestanden en andere bots worden hierdoor niet gewijzigd.

Een onbekend symbool blijft behouden en telt mee bij de dekkingscontrole. Aliassen van dezelfde notering worden eenmaal gescand. Een officiële handelscode bewijst de identiteit van de notering; pas de weekkoerscontrole stelt vast of Yahoo voldoende data levert. Het volledige onderzoek, de beperkingen en de laatste Actions-run staan in [de livecontrole van 15 november](controles/controle_weekly_live_20261115.md).


## Brononderhoud en dekkingscontrole — vervolg 15 november

De volledige a-run 43682958379 scande alle 14162 symbolen zonder blijvende providerstop. De afkeuring kwam van drie bronlijsten onder 80%. De dekkingscontrole blijft actief: verouderde of niet-opvraagbare invoer mag een run afkeuren. De gebruiker accepteert dit en wil de tickerlijsten halfjaarlijks laten nakijken; deze afspraak is geen automatische geplande taak.

Er zijn 33 expliciete codecorrecties toegevoegd aan `weekly/symbols.csv`: drie voor Brussel, drie voor Helsinki en 27 voor Wenen. De bronlijsten van 041 en 059 blijven behouden. Een livecontrole gaf bruikbare weekdata voor 32 vervangende codes; MARI.VI heeft wel historie maar geen bruikbare koersen voor de gekozen week. Marinomed is volgens de beurs sinds september geschorst en op 6 november geschrapt. Een correcte code betekent dus niet automatisch bruikbare weekdata.

Op expliciet verzoek is de gedeelde `tickers_054a.txt` vervangen door de volledige Nasdaq Helsinki-snapshot van 15 november: 40 large-cap-, 54 mid-cap-, 56 small-cap- en 48 First North-noteringen, totaal 198 unieke symbolen. Er is niet geselecteerd op Yahoo-beschikbaarheid. De officiële naam, het ISIN, segment, bronendpoint en peildatum staan in [het register](controles/helsinki_universum_20261115.csv). De oude Helsinki-invoer blijft samen met de oorspronkelijke 041/059-invoer bewaard in [de audit](controles/weekly_coverage_repair_20261115.csv). Niet meer in deze actuele snapshot staan is op zichzelf geen bewijs van delisting. De x-lijst behoudt zijn bestaande selectie.

Dit is een actuele bronlijst, geen historisch universum voor een rendementstest. De bronwijziging geldt voor alle bots die `tickers_054a.txt` inlezen. De bestaande 80%-norm en publicatieblokkering zijn ongewijzigd. Lijst 059 bevat nog Sloveense en Slowaakse invoer zonder bruikbare Yahoo-weekdata; die is niet verwijderd en kan de a-run blijven afkeuren. Telegram blijft behouden.

Een volledige lokale controle van de nieuwe Helsinki-lijst voor 9–13 november leverde **195/198 bruikbare weekrendementen (98,5%)**. Zie [de bron- en dekkingscontrole](controles/controle_weekly_dekking_20261115.md). Dit is geen nieuwe volledige a-productierun; lijst 059 kan het rapport nog steeds afkeuren.


## Deelmarkt-diagnose en korte onvolledigmelding

De 80%-norm is ongewijzigd en geldt voor de hele bronlijst. De aanvullende uitsplitsing van lijst 059 naar Wenen, Ljubljana en Bratislava is een diagnose; een voldoende Weense deelmarkt heft een tekort in de hele lijst niet op. De negen bekende Slowaakse broncodes worden als `Bratislava (broncode)` aangeduid, zonder daarmee hun Yahoo-identiteit te bevestigen. Niet-herkende invoer staat onder `Onbekende markt` en blijft meetellen.

`weekly_dekking_<variant>.csv` heeft daarnaast een kolom `deelmarkt`. Het nieuwe artifact `weekly_marktdekking_<variant>.csv` geeft aantallen, bruikbare reeksen, onbekende uitkomsten, dekkingsverhouding (0–1) en status per lijst/deelmarkt. Bij een providerstop blijft de dekkingsverhouding leeg zolang uitkomsten onbekend zijn; dat is geen gemeten 0% dekking. A/x en opvolg bewaren dit artifact ook na een fout. Deze diagnose doet geen nieuwe koersrequests.

Bij een afgekeurd a/x-rapport bevat Telegram een korte melding met het aantal voldoende lijsten en de tekortschietende lijst(en), inclusief deelmarkt-aantallen. Volledige indicatieve ranglijsten blijven in het als onvolledig gemarkeerde tekst-/e-mailrapport. Geen nieuwe toppers-JSON bij lage dekking. De eerste regels van het rapport bevatten dezelfde diagnose. De actuele dekkingsfunctie en de per-markt-diagnose gebruiken dezelfde norm.
