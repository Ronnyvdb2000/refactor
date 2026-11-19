# Controle forward returns en modelhorizons — 15 november 2026

Scope: `Ronnyvdb2000/refactor`, uitgangspunt `f4d587b`. Geen wijzigingen in `refactor-lab` of weekly-code. Supabase is alleen gelezen; geen labels, selecties of modellen zijn in deze controle aangepast. Er is geen live builder/training gestart.

## Alleen-lezen momentopname

Het actieve project `fantasie` bevat de verwachte tabellen. De koppeling met het GitHub-secret is niet rechtstreeks geverifieerd; dat secret is via deze verbinding niet leesbaar.

| Gegeven | Aantal / datum |
| --- | --- |
| Forward-returnrijen | 41.475 |
| Oudste / nieuwste selectiedatum in forward_returns | 2026-08-10 / 2026-11-04 |
| Ingevulde 5d / 10d / 20d / 30d | 41.199 / 38.517 / 30.663 / 25.712 |
| Ingevulde 60d / unieke datums met 60d | 0 / 0 |
| Technische snapshots | 47.484; 2026-08-10 t/m 2026-11-13 |
| Dubbele technische ticker/datum-sleutels | 0 |
| Opgeslagen Ridge-meta-modellen | 8, allemaal 20d; laatste training 2026-11-02 |
| Selectierijen op zaterdag/zondag | 2.953, over alle strategieën/datums |

De laatste parametermetrics (2026-11-13) bevatten 5d, 10d en 20d met elk tien picks; 30d heeft nul picks. Er is geen 60d-metricsrij in die laatste run. Dit bewijst geen modelkwaliteit of rendementsvoordeel.

## Hersteld

### 60d kwam onnodig laat aan de beurt

De builder gebruikte `int(h * 1.6) + 3` kalenderdagen als toelatingsfilter. Bij 60 handelsdagen is dat 99 kalenderdagen: de eerste selectie van 10 augustus zou pas op 17 november worden geprobeerd. Nul 60d-labels op 15 november kon dus mede door dit filter worden veroorzaakt; het was niet automatisch bewijs dat geen enkele uitkomst al berekenbaar was.

De gedeelde `horizon_utils.py` gebruikt nu `ceil(h * 7/5) + 2`: 9, 16, 30, 44 en 86 kalenderdagen voor 5/10/20/30/60 handelsdagen. Op 15 november komt daardoor 21 augustus in aanmerking voor een 60d-poging. Dit is een kalenderbenadering, geen bewijs van rijpheid: de builder vereist daarnaast de echte latere dagbalken. Unsupported horizons worden vóór SQL-generatie geweigerd.

### Betrouwbaardere labelberekening en voortgang

- Alleen koersdagen vóór vandaag tellen mee; de download heeft een time-out van 15 seconden en een expliciete eindgrens.
- Historie wordt gesorteerd. Dubbele of ongeldige dagdatums blokkeren de ticker. Een ongeldige tussenkoers wordt niet verwijderd om de horizon korter te maken; die horizon blijft leeg. Gaten langer dan zeven kalenderdagen blokkeren de betreffende horizon.
- De selectiedatum moet zelf een koersdag in de historie zijn. Weekend-/feestdagselecties worden niet stil naar de volgende handelsdag verschoven. Er is voor historische weekendselecties een afzonderlijke reconstructie van de werkelijke koersdatum nodig.
- Een rij zonder enig berekenbaar label wordt niet als geslaagde update geteld.
- Bestaande niet-NULL-waarden blijven behouden, ook als een nieuwe download een andere uitkomst zou geven. De bestaande bescherming tegen een afwijkende instapkoers blijft actief. Een expliciete historische herlabeling blijft apart werk.
- Het rapport telt openstaande rijen opnieuw in de database. Een rij waarvan alleen 5d is aangevuld terwijl 60d nog openstaat telt niet als afgerond. Het aantal berekeningen met alle aangevraagde horizons staat apart vermeld.
- De workflow serialiseert runs op dezelfde branch en toont direct loguitvoer. Telegram blijft actief.

### Modellen: handelsdagen zijn geen kalenderdagen

- Parametermodel, beide vergelijkingen over alle horizons en de beide meta-labelclassifiers gebruiken nu minimaal de kalenderbenadering hierboven. Een groter ingesteld embargo blijft behouden. Voor 60d is de minimumscheiding 86 in plaats van 65 kalenderdagen; voor 30d 44 in plaats van 35.
- De Ridge-walk-forward in `meta_models/a_meta_model.py` gebruikte alle eerdere selectiedatums, ook als hun 20d-uitkomst op de testdatum nog niet bekend kon zijn. Nu worden alleen datums vóór het horizonafhankelijke embargo toegelaten, met behoud van het minimumaantal trainingsdatums.
- Het parametermodel trok het embargo af vanaf `min(scoredatum, laatste targetdatum)`. De targetdatum lag al een horizon terug. De grens wordt nu eenmaal afgeleid van de echte scoredatum; ontbrekende targets blijven uitgesloten van de IC-berekening. Hiermee vervalt de onbedoelde tweede wachttijd.
- De productie-scoreloader kiest uitsluitend een opgeslagen Ridge-model met `MODEL_HORIZON` (momenteel 20d). Een later opgeslagen model met een andere horizon kan daardoor niet als 20d-score worden weggeschreven.
- `requirements-analysis.txt` bevat nu yfinance, vereist doordat de meta-entrypoints het gedeelde `common`-package importeren.

## Nog te verifiëren / beperkingen

1. Voer de normale forward-returnworkflow met deze code uit. Controleer aantallen per horizon en de waarschuwingen; 60d wordt alleen gevuld waar geldige historie beschikbaar is. Er zijn tijdens deze controle geen labels gevuld.
2. Training/validatie van 60d blijft wachten op voldoende gelabelde rijen én datums na de embargo-splits. Enkele eerste 60d-labels zijn daarvoor niet voldoende. Bestaande 10d/30d-modelbestanden zijn niet vervangen.
3. De kalenderbuffer is een benadering. Een volledig beurskalenderbewust embargo vereist echte einddatums per label; deze kolommen bestaan nog niet in de huidige tabel. Ook korte ontbrekende koerssegmenten zijn zonder beurskalender niet volledig aantoonbaar.
4. Bestaande historische labels zijn niet opnieuw gevalideerd of herberekend. De bestaande split- en factor-100-eenheidscorrectie is in deze wijziging niet veranderd. Koersbasis (ruw/dividendgecorrigeerd, valuta/subeenheid) is niet voor alle historische selecties vastgelegd. Hiervoor is eerst een controleerbaar herstelplan nodig.
5. Een selectie heeft meer dimensies dan de forward-return-sleutel (onder meer beurs en modelhorizon). Verschillende instapkoersen mogen niet zonder controle samengevoegd worden. De bestaande entry-bescherming blijft van toepassing; geen historische dubbels verwijderd.
6. Ongetoetste historische rendementen en vroegere vergelijkingsresultaten zijn geen bevestiging van modelkwaliteit. De gewijzigde embargo's vereisen een nieuwe evaluatie voordat zulke resultaten opnieuw worden gebruikt.

## Verificatie

`python -m pytest tests/ -q`: 655 tests en 17 deeltests geslaagd; 12 optionele tests overgeslagen. Zeven gewijzigde model-entrypoints slagen met `--help`; Python-compilatie, workflow-YAML en `git diff --check` slagen.

Offline regressies controleren exacte 5d/60d-posities, nog onrijpe horizons, onvoltooide dagbalken, weekenddatums, sortering, dubbele datums, ongeldige tussenkoersen, lange datagaten, de 86-daagse toelatingsgrens, werkelijke openstand, behoud van bestaande labels/instapkoers, causaliteit van de Ridge-folds, eenmalige wachttijd bij het parametermodel en horizongebonden modelkeuze.

De gegenereerde UPSERT is tevens met conflicterende oudere labels uitgevoerd in een geïsoleerde SQLite-geheugendatabase; bestaande 5d bleef gelijk terwijl 60d werd aangevuld, en een afwijkende instapkoers werd geweigerd. De aanvullende PostgreSQL-regressies blijven beschikbaar voor een omgeving met pgserver/lokale sockets.

## Live vervolgcontrole — 15 november 2026

[Forward-returnrun 43685780913](https://github.com/Ronnyvdb2000/refactor/actions/runs/43685780913), met commit `ee79ee5`, is geslaagd en op 15 november om circa 08:02 Belgische tijd afgerond. De daaropvolgende alleen-lezen databasecontrole vond 41.892 forward-returnrijen en **6.573 ingevulde 60d-labels over tien selectiedatums (10–21 augustus 2026)**. De eerdere momentopname met nul 60d-labels is dus achterhaald voor de huidige stand; zij blijft hierboven staan als uitgangspunt van de reparatie.

Dit bevestigt de live aanvulling, niet dat er al voldoende onafhankelijke historie is om een 60d-model verantwoord te trainen en valideren. Er is geen modeltraining geforceerd en er zijn tijdens deze vervolgcontrole geen databasewaarden gewijzigd.

## Alleen-lezen opvolging — 19 november 2026

De actuele tabel `forward_returns` bevat 42.317 rijen, waarvan 9.268 met een
60d-label over twaalf selectiedatums, van 10 tot en met 25 augustus 2026.
De opslag is dus verder aangevuld sinds de vorige controle. Dit bewijst geen
modelkwaliteit; voldoende trainings- en validatiedatums blijven afzonderlijk vereist.
Geen databasewaarden gewijzigd en geen training of analyse herstart.

## XGBoostV3: scheiding van trainingslabels en testperiode — 19 november

De dagelijkse productie-entrypoint `xgboostV3.py` splitste uitsluitend op
selectiedatum. Het rendement van een trainingsrij vlak vóór de testgrens kon
daardoor doorlopen in de testperiode. Dit gold ook voor de 60d-horizon; veel
tickers op slechts twaalf datums lossen die tijdsoverlap niet op.

De trainingsset stopt nu strikt vóór `split_datum - calendar_embargo(horizon)`:
16, 44 en 86 kalenderdagen voor respectievelijk 10d, 30d en 60d. De bestaande
80/20-keuze van de testgrens en alle rijen in de testperiode blijven behouden.
Rijen in de tussenruimte worden niet gebruikt voor training. Hoofdmodel en
relatief controlemodel gebruiken exact dezelfde gescheiden sets. De grens en
het aantal uitgesloten rijen worden gelogd. De bestaande minimumrijentellingen
worden na deze scheiding opnieuw toegepast; onvoldoende data slaat de horizon
over vóór fitting en modelpublicatie.

Dit is dezelfde kalenderbenadering als de andere horizoncontroles in deze repo,
geen exacte beurskalender of bewijs van onafhankelijke testwaarnemingen. Een
label-einddatum per ticker is niet beschikbaar in deze trainingsquery. Gedeelde
marktbewegingen en overlappende horizons binnen de testperiode blijven bestaan.

Vijf synthetische regressies controleren de daadwerkelijke invoer van beide
modeltrainingen voor alle drie horizons, de strikte grensdatum en het overslaan
van een dichte twaalfdaagse 60d-dataset zonder het bestaande model te wijzigen.
Training, opslag en notificaties zijn tijdens de controles niet echt uitgevoerd.

De afzonderlijke proefvariant `xgboostV3_gecontroleerd.py` had al een eigen
embargoperiode en blijft apart. Deze correctie vervangt geen productiecode door
die proefvariant, wijzigt geen Telegraminstellingen en start geen hertraining.
Bestaande modelbestanden en historische metrieken blijven behouden; zij zijn
hiermee niet achteraf op deze nieuwe scheiding gevalideerd. De eerstvolgende
productietraining kan terecht minder horizons bijwerken dan voorheen.

Volledige offline suite na deze correctie: **784 tests en 19 deeltests geslaagd**,
dertien optionele tests overgeslagen. Uitgaande socketverbindingen waren geblokkeerd.
Workflowaudit: 65 workflows zonder fouten of waarschuwingen. `git diff --check` slaagt.

## Forward-returnlog van 19 november: instapconflicten en ontbrekende koersdagen

Het aangeleverde log op `3e79f13` start met 1784 openstaande ticker/datum/strategie-
rijen en verwerkt 652 unieke tickers. Er worden 378 rijen bijgewerkt. De 985
waarschuwingen zijn: 792 zonder koers op de selectiedatum (veel weekenddatums),
169 geweigerde updates wegens een andere instapkoers, dertien ontbrekende
histories en elf ongeldige koersvensters/historiegaten. Een bijgewerkte rij hoeft
nog niet alle langere horizons te bevatten; de echte openstand wordt opnieuw
uit de database gelezen.

### Alleen-lezen onderzoek

Een actuele vergelijking van de canonieke eerste niet-NULL-selectie met de
bewaarde labelinstap vond 172 conflicten, over 125 marktsentiment-, 44 kasstroom-
en drie seizoensrijen. Dat is de hele databasevergelijking, niet uitsluitend de
in deze ene run afgehandelde subset. Voor 171 conflicten bestaat nog een andere
selectierij met een koers die aansluit op de bewaarde labelinstap. Dit bewijst
een verschil in bronkeuze, niet welke koers historisch de juiste instap was.
De huidige bronregel blijft daarom expliciet de eerste selectie; er wordt geen
andere selectie gekozen alleen om een bestaand label passend te maken.

### Door gebruiker gevraagde opschoning

Op verzoek zijn de **172 conflicterende rijen uit `forward_returns` verwijderd**,
zodat een volgende builderrun opnieuw kan berekenen op de bestaande canonieke
bronselectie. Vooraf is een volledige herstelkopie met alle labelkolommen en
rijvingerafdrukken buiten deze publieke repository opgeslagen. De DELETE was
begrensd tot de gekopieerde sleutels en ongewijzigde rijvingerafdrukken, en alleen
als de instap op het moment van verwijderen nog conflicteerde. Verwijderd:
125 `bot_01marktsent`, 44 `bot_01kasstr`, drie `bot_01repititief`.
Een nacontrole vond nul resterende instapkoersconflicten.

De tabel `selecties`, weekendselecties en overige labels zijn niet door deze
DELETE gewijzigd. Geen koersdatum of instapkoers is aangepast. De verwijdering
is geen bewijs dat historische bronkoersen of oude modelprestaties daarmee
volledig gevalideerd zijn; modellen zijn niet hertraind.

### Diagnose en voorkómen van herhaalde onbruikbare aanvragen

De builder leest voortaan ook de bewaarde instapkoers bij de openstaande rij.
Een conflict met de gekozen bron wordt vóór downloaden geblokkeerd, met dezelfde
tolerantie als de bestaande UPSERT. De UPSERT zelf houdt zijn tweede controle
tegen conflicten tijdens een gelijktijdige verwerking. Zulke rijen blijven in
de totale openstand staan; zij verdwijnen alleen uit de downloadpogingen.

Iedere openstaande rij krijgt een regel in `results/forward_returns_diagnose.csv`,
met bronkoers, opgeslagen instap, weekendvlag, status, reden en detail. Ontbrekende
koersdagen worden voor beoordeling gemarkeerd, niet verschoven naar vrijdag of
maandag. Weekenddatums worden niet blind geweigerd: als de provider daadwerkelijk
een sessie op die datum levert, blijft de bestaande berekening beschikbaar.
De status `berekend` is géén opslagbevestiging; het log en de database-openstand
blijven leidend voor opgeslagen/afgeronde rijen.

De workflow bewaart het artifact `forward-returns-diagnose` met `if: always()`.
Het volledige Telegram-rapport wordt ook gelogd, inclusief geblokkeerde rijen en
de werkelijke openstand. Bestaande Telegram- en e-mailaanroepen blijven behouden.

Negen nieuwe offline regressies toetsen conflicttolerantie, geen download/write
bij een conflict, behouden bronkeuze, verwerking van een andere geldige rij,
weekenddatums met en zonder echte providerbalken en diagnose na een opslagfout.
Volledige suite: **822 tests en 19 deeltests geslaagd**, dertien optionele tests
overgeslagen, met uitgaande socketverbindingen geblokkeerd. Workflowaudit:
65 workflows zonder fouten of waarschuwingen. YAML/shellsyntax en whitespace
gecontroleerd. Geen nieuwe live labelscan of modeltraining gestart.

### Herhaalde gedeeltelijke resultaten tellen alleen bij echte aanvulling

De laatste run berekende 154 gedeeltelijke resultaten terwijl nul selecties volledig
werden afgerond. De UPSERT telde ook bestaande waarden opnieuw als bijgewerkt en
vernieuwde daarbij de opslagdatum. Nu wordt een bestaande rij alleen bijgewerkt
als minstens één ontbrekend veld daadwerkelijk wordt aangevuld. De bescherming
van bestaande labels en de controle op de instapkoers blijven actief. Een SQL-resultaat
zonder wijziging wordt niet meer ten onrechte als instapconflict gemeld.

Gerichte offline validatie: 47 tests geslaagd, tien optionele databasetests
overgeslagen. De regressie voert de gegenereerde UPSERT uit en controleert dat
herhaling geen wijziging telt en de bestaande opslagdatum behoudt. Telegram blijft
behouden. Geen historische selecties verwijderd of datums verschoven.

### Opschoning probleemgevallen op 19 november 2026

Op expliciet verzoek zijn de 1.237 gediagnosticeerde combinaties met ontbrekende
selectiedatumkoers, ontbrekende historie of geen bruikbare horizon verwijderd:
1.262 bronselecties (inclusief dubbele bronrijen) en 927 bijbehorende rendementen.
Vooraf is een volledige herstelkopie buiten de publieke repository opgeslagen.
De transactie controleerde alle rijvingerafdrukken en verwachte aantallen;
nacontrole vond nul resterende bron- of rendementsrijen voor deze sleutels.
Dit corrigeert geen providersymbolen of toekomstige bronproblemen.

Na opschoning: nul kandidaten zonder 5d-label; respectievelijk 15, 29, 27 en 98
kandidaten zonder 10d-, 20d-, 30d- en 60d-label. Dit zijn overlappende groepen op
basis van kalendergrenzen, geen bewijs van 60 beschikbare koerssessies.
