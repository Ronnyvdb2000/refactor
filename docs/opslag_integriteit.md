# Opslagintegriteit — 14 november 2026

Scope: `Ronnyvdb2000/refactor`. Alleen broncode aangepast; geen bestaande Supabase-rijen verwijderd, gewijzigd of opnieuw gelabeld. Geen wijzigingen in refactor-lab. De 60-daagse labels mogen verder rijpen.

## Bevindingen uit de alleen-lezen controle

Het actieve Supabase-project heet `fantasie`; `refactor-db` is inactief. Via de verbinding is het GitHub-secret niet leesbaar, dus de koppeling tussen repository en database is niet daarmee bewezen.

- De eerste telling vond 4.307 extra rijen bij groepering op ticker, datum, strategie en beurs. Dit is een screeningsaantal, geen verwijderlijst: verschillende modelhorizonten zijn geldige afzonderlijke selecties.
- `db_logger.py` voegde bij iedere aanroep een rij toe. Een herstart of dubbele run kon daardoor dezelfde selectie opnieuw opslaan.
- Het parametermodel gebruikte `ON CONFLICT DO NOTHING`, maar de tabel had alleen een unieke sleutel op het automatisch gegenereerde id. Dit voorkwam geen inhoudelijke herhalingen.
- De combinatiebot gaf expliciet `koers=None` door. Het parametermodel kon een lege koers kiezen vóór een geldige bronrij. Van de 123 eerder gevonden lege parametermodelkoersen hadden er slechts tien een positieve bronkoers op dezelfde datum; oudere koersen worden niet als een nieuwe peildatum gepresenteerd.
- Rendementsverschillen tussen strategieën zijn niet automatisch fouten: instapkoersen en peildatums kunnen verschillen. Voor een aantal afwijkingen tussen begin-/eindkoers en rendement past de impliciete factor bij splitcorrectie (bijvoorbeeld circa 7 bij AMD.TO). Historische waarden zijn daarom niet automatisch herberekend.

## Herstel in de code

### Herstarts

De gedeelde logger gebruikt de sleutel `(ticker, datum, strategie, beurs, parameters.horizon, parameters.horizon_days)`. Een transactiegebonden advisory lock wacht op een andere schrijver met dezelfde sleutel. Daarna controleert een afzonderlijke SELECT of de selectie bestaat. Dit vereist de standaard READ COMMITTED-transactie-isolatie. Ook bulkopslag volgt dezelfde sleutelvolgorde om wederzijdse wachtrijen te vermijden.

- Een bestaande geldige snapshot wordt bij een herstart behouden.
- Een bestaande rij met ontbrekende/ongeldige koers kan met een geldige koers worden aangevuld, zonder nieuwe rij.
- Verschillende horizonten en beurzen blijven apart.
- Bulkrijen gebruiken savepoints: één mislukte rij maakt latere geldige rijen niet ongeldig. Aantallen worden pas na commit bevestigd.
- Een tijdelijke verbindingsfout blokkeert volgende aanroepen niet permanent. De verbindingstime-out is 15 seconden; de lock-wachttijd is maximaal 15 seconden.

Dit is preventie in de schrijfcode, geen database-UNIQUE-constraint. Oude reeds lopende checkouts, andere repositories of externe rechtstreekse schrijvers gebruiken dit protocol mogelijk nog niet. Voor een databasebrede garantie moet eerst een herzienbare keuze over de historische rijen en definitieve sleutel worden gemaakt.

### Koersen

De combinatiebot bewaart de koers en koersdatum uit zijn bestaande ATR-download. Alleen afgeronde, maximaal zeven kalenderdagen oude koersdagen worden gebruikt. Er is geen extra koersdownload; de precisie van de bronkoers blijft behouden. De bronbasis staat in de parameters als `yahoo_auto_adjusted_close`. Een ontbrekende opslagbevestiging geeft een fout.

Het parametermodel kiest deterministisch een positieve, eindige selectiekoers van exact de feature-/scoredatum. Het gebruikt de gedeelde logger met afzonderlijke horizons. Picks zonder zo'n koers blijven zichtbaar in de modeluitvoer en log, maar worden niet als onvolledige selectierij opgeslagen. Een volledige bron voor niet eerder geselecteerde tickers vereist een afzonderlijke uitbreiding van de feature-store met koers en koersbasis.

### Forward returns

Een upsert met een wezenlijk andere `entry_koers` wordt geweigerd en zichtbaar gelogd. De bestaande beginwaarde en labels blijven samen behouden. Ontbrekende langere horizons mogen bij dezelfde beginwaarde verder worden aangevuld, zonder eerdere labels met NULL te wissen. Split- en eenheidscorrecties zijn niet gewijzigd.

## Verificatie

`python -m pytest tests/ -q`: 531 tests en 17 deeltests geslaagd; 9 PostgreSQL-verbindingstests overgeslagen omdat lokale sockets in de testomgeving niet beschikbaar zijn. De nieuwe suite bevat onder meer herstarts, afzonderlijke horizons, bulk-savepointafhandeling, koersdatum/precisie en een test met gelijktijdige verbindingen voor uitvoering waar sockets beschikbaar zijn.

De werkelijk gegenereerde logger- en forward-return-SQL is daarnaast uitgevoerd in een geïsoleerde PostgreSQL 16.2-standalonebackend zonder netwerkverbindingen. De upsertcontroles bevestigen behoud van eerdere labels en weigering van een gewijzigde instapkoers. Live gelijktijdige schrijfacties zijn hier niet getest.

Optioneel voor de verbindingstests: installeer `pgserver` naast de testdependencies en voer `python -m pytest tests/test_storage_integrity.py -q` uit in een omgeving die lokale PostgreSQL-sockets toestaat.

## Vervolg

Controleer na runs met de nieuwe code of de teller per natuurlijke sleutel gelijk blijft bij herhaling. Stel daarna een afzonderlijk herstelvoorstel voor historische dubbels en ontbrekende koersbasis op; verwijder nooit blind alle rijen met dezelfde ticker/datum/strategie.


## Modulepad

De gedeelde logger staat in `common/db_logger.py`. Gebruik `from common.db_logger import log_selectie` of `from common import db_logger`. Alle interne imports zijn aangepast; het oude rootbestand is verwijderd. De opslaglogica, validatie, locks, savepoints en controles tegen dubbele selecties zijn bij deze verplaatsing behouden.
