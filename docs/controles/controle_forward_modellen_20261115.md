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
- De Ridge-walk-forward in `a_meta_model.py` gebruikte alle eerdere selectiedatums, ook als hun 20d-uitkomst op de testdatum nog niet bekend kon zijn. Nu worden alleen datums vóór het horizonafhankelijke embargo toegelaten, met behoud van het minimumaantal trainingsdatums.
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
