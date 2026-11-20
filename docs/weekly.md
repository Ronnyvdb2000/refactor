# Weekly bots

De weekly scripts staan in `weekly/`. Start vanuit de repository-root als module; tickerbestanden en JSON-uitvoer blijven in de hoofdmap. `weekly/shared.py` bevat alleen weekgrenzen, rapportage en koerscache voor deze groep.

| Script | Workflow | Functie |
| --- | --- | --- |
| `weekly/weekly_report.py` | `weekly_run.yml` | Rapport voor 01–09 en 041a–059a; `weekly/laatste_toppers.json` |
| `weekly/weekly_report_x.py` | `weekly_run_x.yml` | Rapport voor 01–09 en 041x–059x; `weekly/laatste_toppers_x.json` |
| `weekly/selection_storage.py` | Via beide a/x-rapporten | Eigen topvijf >10% en parameters vóór de week naar Supabase |
| `weekly/weekly_report_opvolg.py` | Geen; losse scan vervallen | Herbruikbare indicatorfuncties voor historische parameters |
| `weekly/weekly_db_opvolg.py` | Via a/x-rapporten en correlaties | Opslag en ranks op peildatum; geen zelfstandige workflow |
| `weekly/weekly_rep_backtest.py` | `weekly_runV2.yml` | Retrospectieve technische verkenning van weekwinnaars |
| `weekly/analyseer_weekly_correlaties.py` | `analyse_correlaties_weeklytop.yml` | Handmatige beschrijvende correlaties; CSV en e-mail |

Voorbeeld: `python -m weekly.weekly_report`. Dit start de echte scan met notificaties. Alleen `python -m weekly.weekly_rep_backtest --help` toont hulp zonder uitvoering.

## Weekgrenzen en rendement

De a-rapportage start vrijdag 23:00 UTC; de x-rapportage om 23:15 UTC. In België is dat zaterdag 00:00 in de winter en 01:00 in de zomer. Een handmatige run gebruikt de laatste volledige week; vóór vrijdag 23:00 UTC geldt nog de vorige week.

Weekprestatie loopt van de laatste beschikbare slotkoers vóór maandag tot het laatste beschikbare slot van de week, met aangepaste koersen. Vijf slotkoersen van maandag tot vrijdag zouden alleen vier dagrendementen meten. De basis mag maximaal zeven kalenderdagen vóór maandag liggen; de eindkoers maximaal drie dagen vóór vrijdag. Ontbrekende of oudere koersen worden uitgesloten en aantallen verschijnen per lijst. Positieve stijgers en negatieve dalers worden apart gerangschikt; de grootste daler krijgt rang 1. De neutrale steekproef blijft per lijst/week reproduceerbaar.

Elke unieke ticker wordt per proces gecachet. Downloads gebruiken batches van maximaal 20, twee workers en een timeout van 15 seconden. Ontbrekende data krijgt maximaal één batchretry; er volgen geen individuele retries. Bij een rate-limit volgt één herstelpauze van 15 seconden en bij herhaling stopt de scan. Vóór elke batch zit twee seconden pauze, ook tussen opeenvolgende laadacties. De a/x-rapporten scannen hun eigen universum en halen lange historie alleen voor hun geselecteerde topvijf-stijgers boven 10% op. Er is geen aparte opvolgscan meer. Verschillende runners kunnen samen nog steeds providerlimieten raken.

## Hervatten na een providerstop

De a/x-rapportworkflows zetten `WEEKLY_CHECKPOINT_DIR=.weekly-cache` en zoeken eerst de laatst bewaarde voortgang voor dezelfde week en dezelfde invoer/code. Als die sleutel ontbreekt, kan de laatst bewaarde cache van dezelfde week worden gebruikt. Elk bestand wordt alsnog op ticker en periode gevalideerd; gewijzigde of nieuwe tickers worden afzonderlijk opgehaald. Zo maakt een broncorrectie niet alle bestaande weekkoersen onbruikbaar. Na elke volledig afgehandelde batch worden alleen de reeksen met een bruikbaar weekrendement atomair als CSV bewaard. Een volgende uitvoering downloadt die reeksen niet opnieuw. Ontbrekende en verouderde weekdata worden opnieuw geprobeerd; een onderbroken batch telt niet als afgerond. De hervatcache is optioneel en staat los van de gepubliceerde toppers-JSON.

De cache is gescheiden per historieperiode en eindweek, met controle op ticker, koerskolommen en datumbereik. Onleesbare bestanden worden opnieuw opgehaald. De workflows bewaren de cache ook na een providerstop. De scanstap heeft een limiet van 75 minuten binnen het jobbudget van 90 minuten, zodat er ruimte blijft voor cache- en artifactopslag. Bij een harde runneruitval kan de laatste voortgang alsnog verloren gaan; Actions-cache kan bovendien ontbreken of verwijderd zijn. Een nieuwe week begint met nieuwe koersdata.

Dit verlaagt de verzoekdruk en voorkomt bij een gewone providerstop herhaald downloaden van eerder gevalideerde data. Het garandeert geen Yahoo-toegang en verandert de 80%-controle niet. Een mislukte run blijft rood en publiceert geen nieuwe toppers. Er is geen automatische herstartlus.

## Telegram, e-mail en workflow

Telegram blijft behouden voor de weekly rapporten, opvolg, winnaarsanalyse en correlatie-analyse. Secrets blijven in de YAML staan. Rapportberichten gebruiken tekst zonder Markdown-parsing; lange berichten worden opgedeeld. Verzendfouten worden gelogd. De a/x-rapporten leveren daarnaast tekst-artifacts en e-mail wanneer de e-mailsecrets beschikbaar zijn. De bestaande e-mail van correlaties blijft behouden. Bij volgende botcontroles wordt Telegram behouden volgens de gebruikersinstructie van 14 november 2026.

Beide JSON-schrijvende workflows delen een concurrency-groep en bewaren uitvoer alleen na succes op main. Git gebruikt rebase en gewone pushes; een conflict wordt zichtbaar in plaats van met `-X ours` overschreven. De backtest volgt via `workflow_run` op een geslaagd Weekly Report en blijft handmatig startbaar. De output van de oude JSON-lijsten zonder weekdatum wordt geweigerd; draai eerst de nieuwe Weekly Report. Iedere nieuwe topper bevat weekgrenzen, werkelijke koersdatums en `data_version=3`.

## Historische betrouwbaarheid

De a/x-rapporten bewaren één snapshot van de laatste geldige koersdag vóór de gekozen week (`dag_index=0`). Oudere opvolgdata met dag_index 1..5 blijft bewaard. Ranks komen van dezelfde beurs, zijn maximaal 30 dagen oud en moeten op de peildatum al opgeslagen zijn geweest. Ontbrekende ranks blijven NULL. Actuele Yahoo-fundamentals en actuele short/float-data worden niet naar historische dagen gekopieerd. Ook Debt/Equity wordt niet meer onder de naam Debt/EBITDA opgeslagen.

Een herhaalrun behoudt de eerste selectie per ticker/beurs/week; een ander type veroorzaakt geen extra rij. Parameter-upserts vullen alleen NULL aan. Databasefouten worden doorgegeven, zodat een mislukte export geen groen succesrapport oplevert. Bestaande gegevens zijn tijdens deze refactor niet aangepast of verwijderd.

De correlatie-analyse gebruikt alleen weken vanaf 16 november 2026. Oudere waarden met de eerdere definities blijven bewaard en zijn uitgesloten. De analysetarget is het rendement tussen de eerste en laatste werkelijk opgeslagen dagslotkoersen binnen de week, dus niet het gehele vorige-vrijdag-tot-vrijdag rapport-rendement. Dubbels worden in de analyse uitgesloten en actuele fundamentals worden niet als historische verklarende variabelen getest. De steekproef is achteraf geselecteerd en ticker-weken zijn afhankelijk: p-waarden/FDR blijven verkennend en bewijzen geen voorspelkracht.

De zogenoemde backtest blijft een retrospectieve winnaarsanalyse. De piekdag wordt achteraf gekozen; dit is geen onafhankelijke rendementstest van een verhandelbare strategie. Koersdata stopt bij de opgeslagen weekgrens; indicatoren stoppen vóór de gevonden piekdag. De eerste dag van het zoekvenster telt nu mee. Short/float blijven historisch onbekend.

## Live controle van 14 november: onvolledige brondata

De eerste grote run na de verplaatsing duurde 36 minuten en 51 seconden en publiceerde ondanks veel providerfouten een JSON-lijst. De kleine eerdere test had deze schaalproblemen niet blootgelegd. Frankfurt leverde 165/1418 bruikbare tickers; Warschau 26/361 en Helsinki 67/188. Een groene YAML betekende hier alleen dat het proces voltooid was.

De weekly invoer normaliseert nu kale symbolen uit lijst 043 naar .DE en Ierse .I-symbolen uit 041 naar .IR. De bronbestanden zelf blijven behouden. Symbolen uit andere lijsten worden niet op basis van een gok hernoemd. Daardoor is een Amerikaanse AMD geen Frankfurt-koers meer. Lijst 055 wordt als CSE aangeduid.

Een niet-lege beurs-/themagroep moet minstens 80% bruikbare weekrendementen hebben; bij kleine lijsten is maximaal één ontbrekende ticker toegestaan. Lege kwaliteitslijsten worden overgeslagen. Alleen voldoende gedekte groepen krijgen een ranglijst, toppers-JSON en database-selecties. Bij ontbrekende groepen krijgt het rapport de status GEDEELTELIJK, met een geslaagde workflow zolang er een voldoende gedekte groep met bruikbare data is. De automatische winnaarsanalyse gebruikt uitsluitend deze gepubliceerde selecties. Bij een providerstop of zonder enige voldoende gedekte groep blijft de workflow rood en wordt de bestaande JSON niet vervangen. Telegram vermeldt de beperking. Ook bij geslaagde lijsten staat het aantal bruikbare tickers in het rapport en de JSON; 80% is een minimum voor indicatieve ranglijsten, geen garantie van volledige dekking.

Het artifact weekly_dekking_<variant>.csv bevat per lijst en ticker of een bruikbaar weekrendement beschikbaar was. Bij een providerstop blijft de al gemeten dekking behouden. De kolom `status` onderscheidt `bruikbaar`, `geen_bruikbare_weekdata`, `download_afgebroken` en `niet_opgevraagd`. Voor de laatste twee blijft `bruikbaar` leeg: niet onderzocht betekent niet bewezen onbruikbaar. De diagnose gebruikt uitsluitend de bestaande cache en start geen extra downloads. Dit geldt ook voor opvolg. De oude versie-2-JSON is niet meer geldig voor de winnaarsanalyse; die vereist een nieuwe versie-3-snapshot.

## Onderzoek Warschau en Helsinki — 15 november

De weekly invoer gebruikt daarnaast expliciete beurscodes uit `weekly/symbols.json`. Elke koppeling bevat de oorspronkelijke naam, handelscode, ISIN, bron-URL en controledatum. Voor Warschau zijn de naam en handelscode op dezelfde officiële GPW-bedrijfskaart gecontroleerd, met het ISIN uit de actuele bedrijvenlijst. Voor Helsinki staan alleen afzonderlijk geverifieerde koppelingen in het register. De normalisatie geldt uitsluitend voor de a/x-lijsten van de betreffende beurs. De gemeenschappelijke bronbestanden en andere bots worden hierdoor niet gewijzigd.

Een onbekend symbool blijft behouden en telt mee bij de dekkingscontrole. Aliassen van dezelfde notering worden eenmaal gescand. Een officiële handelscode bewijst de identiteit van de notering; pas de weekkoerscontrole stelt vast of Yahoo voldoende data levert. Het volledige onderzoek, de beperkingen en de laatste Actions-run staan in [de livecontrole van 15 november](controles/controle_weekly_live_20261115.md).


## Brononderhoud en dekkingscontrole — vervolg 15 november

De volledige a-run 43682958379 scande alle 14162 symbolen zonder blijvende providerstop. De afkeuring kwam van drie bronlijsten onder 80%. De dekkingscontrole blijft actief: verouderde of niet-opvraagbare invoer sluit de betreffende groep uit. De aanvankelijke blokkering van het gehele rapport is later vervangen door publicatie per voldoende gedekte beurs (zie hieronder). De gebruiker wil de tickerlijsten halfjaarlijks laten nakijken; deze afspraak is geen automatische geplande taak.

Er zijn 33 expliciete codecorrecties toegevoegd aan `weekly/symbols.json`: drie voor Brussel, drie voor Helsinki en 27 voor Wenen. De bronlijsten van 041 en 059 blijven behouden. Een livecontrole gaf bruikbare weekdata voor 32 vervangende codes; MARI.VI heeft wel historie maar geen bruikbare koersen voor de gekozen week. Marinomed is volgens de beurs sinds september geschorst en op 6 november geschrapt. Een correcte code betekent dus niet automatisch bruikbare weekdata.

Op expliciet verzoek is de gedeelde `tickers_054a.txt` vervangen door de volledige Nasdaq Helsinki-snapshot van 15 november: 40 large-cap-, 54 mid-cap-, 56 small-cap- en 48 First North-noteringen, totaal 198 unieke symbolen. Er is niet geselecteerd op Yahoo-beschikbaarheid. De officiële naam, het ISIN, segment, bronendpoint en peildatum staan in [het register](controles/helsinki_universum_20261115.json). De oude Helsinki-invoer blijft samen met de oorspronkelijke 041/059-invoer bewaard in [de audit](controles/weekly_coverage_repair_20261115.json). Niet meer in deze actuele snapshot staan is op zichzelf geen bewijs van delisting. De x-lijst behoudt zijn bestaande selectie.

Dit is een actuele bronlijst, geen historisch universum voor een rendementstest. De bronwijziging geldt voor alle bots die `tickers_054a.txt` inlezen. De 80%-norm blijft actief. Lijst 059 bevat nog Sloveense en Slowaakse invoer zonder bruikbare Yahoo-weekdata; die blijft zichtbaar maar blokkeert na de aanpassing hieronder alleen de eigen deelmarkt. Telegram blijft behouden.

Een volledige lokale controle van de nieuwe Helsinki-lijst voor 9–13 november leverde **195/198 bruikbare weekrendementen (98,5%)**. Zie [de bron- en dekkingscontrole](controles/controle_weekly_dekking_20261115.md). De latere Actions-run 43715010588 bevestigt 196/198 (99,0%) voor Helsinki. Deze run gebruikte nog de globale blokkering en faalde op lijst 059.


## Deelmarkt-diagnose en korte onvolledigmelding

Op verzoek wordt nu per voldoende gedekte beurs gepubliceerd. De 80%-norm is ongewijzigd. Lijst 059 wordt voor rapportage én opvolg gesplitst in onafhankelijke groepen Wenen, Ljubljana en Bratislava; de overige beurs-/themalijsten blijven afzonderlijke groepen. Bij de uitkomsten van run 43715010588 kwalificeert Wenen (63/68; 92,6%), terwijl Ljubljana (0/17) en Bratislava (0/9) worden overgeslagen. Dit is een toepassing van de nieuwe regel op die gemeten aantallen, geen reeds geslaagde nieuwe productierun. De negen bekende Slowaakse broncodes worden als `Bratislava (broncode)` aangeduid, zonder daarmee hun Yahoo-identiteit te bevestigen. Niet-herkende invoer staat onder `Onbekende markt` en blijft meetellen.

`weekly_dekking_<variant>.csv` heeft daarnaast een kolom `deelmarkt`. Het nieuwe artifact `weekly_marktdekking_<variant>.csv` geeft aantallen, bruikbare reeksen, onbekende uitkomsten, dekkingsverhouding (0–1) en status per lijst/deelmarkt. Bij een providerstop blijft de dekkingsverhouding leeg zolang uitkomsten onbekend zijn; dat is geen gemeten 0% dekking. A/x en opvolg bewaren dit artifact ook na een fout. Deze diagnose doet geen nieuwe koersrequests.

Bij een gedeeltelijk a/x-rapport bevat Telegram de dekkingssamenvatting én het volledige weekrapport met stijgers en dalers van alle gepubliceerde groepen. Lange rapporten worden in meerdere berichten verstuurd. Per berichtdeel vermeldt het Actions-log of Telegram het heeft geaccepteerd; een mislukking verschijnt als workflowwaarschuwing. Acceptatie door de Telegram-API bevestigt geen lees- of pushmelding op het toestel. Het tekst-/e-mailrapport bevat uitsluitend ranglijsten van voldoende gedekte groepen. De nieuwe toppers-JSON vervangt de oude snapshot en bevat uitsluitend actuele winnaars uit die groepen, met `rapport_status=gedeeltelijk` (anders `volledig`). Wenen krijgt het eigen beurslabel `059 Wenen` en eigen dekkingsaantallen; er wordt geen ranglijst voor de gecombineerde 059-lijst gemaakt. Opvolg gebruikt dezelfde groepsindeling en norm. De volledige invoer blijft in de dekkingsartifacts staan, ook onbekende codes. Brononderhoud blijft nodig; een groene gedeeltelijke run betekent geen volledige marktdekking.


## Uitbreiding a en x — 15 november 2026

Beide rapportbots bewaren per voldoende gedekte beurs/themagroep maximaal hun eigen vijf grootste stijgers, uitsluitend bij een onafgerond weekrendement strikt boven 10%. Rapport en JSON blijven alle gebruikelijke stijgers/dalers tonen; alleen de databaseverzameling heeft de 10%-filter. Geen dalers of neutrale steekproef meer. Na selectie wordt uitsluitend voor deze tickers lange historie opgehaald, afgekapt vóór maandag. Ontbrekende historische fundamentals blijven NULL. Een ontbrekende historische reeks geeft een waarschuwing; de selectie en alle beschikbare snapshots worden opgeslagen en de workflow gaat door met het wegschrijven van de toppers-JSON. Het volledige Telegram-rapport wordt vóór deze extra fase verzonden. Echte verbindings-/schrijffouten blijven fataal.

`bronselectie` is `a`, `x` of `a,x`. Een transactielock en een unieke gedeeltelijke index voorkomen nieuwe rapportdubbels per ticker/beurs/week. Bestaande snapshots blijven behouden; alleen bronlidmaatschap en ontbrekende parameters worden aangevuld. Historische opvolgrijen zonder bewezen bron blijven NULL; bestaande historische dubbels zijn niet verwijderd. De parameter-uniciteit omvat voortaan de doelweek, zodat een pre-week-vrijdag niet botst met een oudere dagwaarneming. Een herhaalrun herstelt ontbrekende opslag. Geen actuele kenmerken terugdateren.

Schemawijziging: `migrations/weekly_report_bronselectie.sql`. Beide workflows geven `SUPABASE_DB_URL` door. De losse `weekly_run_opvolg.yml` is verwijderd; de oude module weigert een nieuwe universumscan. De bestaande correlatiebot voor dagontwikkelingen leest alleen weekdagen binnen de doelweek en vermengt de nieuwe pre-week-snapshots niet met die oude analysemethode.


Validatie van deze uitbreiding: 60 weekly-tests geslaagd, inclusief uitvoering van de gegenereerde opslag-SQL in PostgreSQL (bronmerge, behoud van eerste snapshot en aanvullen van parameters). Volledige suite: 735 tests en 17 deeltests geslaagd; 9 verbindingstests overgeslagen. De migratie is toegepast en de twee unieke indexen zijn gecontroleerd in het actieve project `fantasie` (`fxdklbdykdnvhmzkyyma`). RLS blijft ingeschakeld. De waarde van het bestaande GitHub-secret is niet uitleesbaar; een nieuwe Actions-run moet de daadwerkelijke verbinding en opslag via dat secret nog bevestigen.


## Ontbrekende parameters blokkeren het rapport niet — 15 november 2026

Run 43749559756 verwerkte 89 selecties en 83 pre-week-snapshots, maar eindigde rood door zes ontbrekende historische parameterreeksen. Beide rapportbots behandelen dat nu als waarschuwing. Telegram bevestigt de geslaagde opslag van selecties en beschikbare parameters, met de ontbrekende tickers. Het proces eindigt normaal zodat de workflow de nieuwe toppers-JSON commit. Er worden geen parameters verzonnen of ongeldig bevonden koersreeksen toch gebruikt. Een volgende run kan ontbrekende parameters aanvullen. Echte databasefouten en onvoldoende dekking voor publicatie blijven fouten.


De twee topperssnapshots staan onder `weekly/`. Rapportpublicatie, de retrospectieve backtest en de git-publicatiestappen gebruiken dezelfde nieuwe paden. Tickerlijsten, koerscaches en overige rapportpaden zijn niet verplaatst. De snapshots worden nog steeds pas na de bestaande dekkingscontroles atomair vervangen.
