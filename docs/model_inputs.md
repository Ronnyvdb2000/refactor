# Modelinvoer en datumcontrole — 15 november 2026

Scope van deze ronde: Ridge-meta-evaluatie, Ridge-training/-scoring en productie-XGBoost-scoring in `Ronnyvdb2000/refactor`. Geen wijzigingen aan weekly-bots, tickerlijsten of `refactor-lab`. Geen live training of scoring gestart en geen bestaande databasegegevens aangepast.

## Gevonden fouten

De Ridge-scorer gebruikte de meest recente technische rij **vóór of op** de selectiedatum, zonder maximumleeftijd. De training gebruikte juist een exacte datumjoin. Een rij kon daardoor een nieuwe scoredatum krijgen terwijl de modelinvoer van een andere dag was. XGBoost stond eveneens een technische terugval tot zeven dagen toe. De inner join in die query verwijderde selecties zonder technische data vóórdat de invoerdekking werd geteld.

Ridge-training kon bovendien met nul opgehaalde rijen eindigen met een succesvolle exitcode. De gecombineerde train/score-workflow kon dan doorgaan met het vorige model, hoewel de gevraagde hertraining niets had opgeleverd. In de oude XGBoost-scorelus werden individuele voorspellingsfouten opgevangen waarna de overige resultaten toch als geslaagde run konden worden opgeslagen.

## Alleen-lezen controle van de bestaande data

Momentopname in het actieve Supabase-project `fantasie`; de GitHub-secretverbinding is niet rechtstreeks uitgelezen/gevalideerd.

| Selectiedatum | Ruwe selectierijen | Technische rij op dezelfde dag | Complete Ridge-features vóór selectieopschoning |
| --- | --- | --- | --- |
| 2026-11-14 | 1.665 | 0 | 0 |
| 2026-11-13 | 1.812 | 1.754 | 1.453 |
| 2026-11-12 | 381 | 95 | 65 |

De nul op 14 november bewijst niet dat alle aandelen ongeldig zijn: de selectiedatum is zaterdag, terwijl de technische snapshots een handelsdatum gebruiken. Zo'n historische selectie mag niet stilzwijgend worden verschoven. Een wijziging van de opgeslagen datum of koersbasis vereist een afzonderlijke reconstructie van de bron. Bij de nieuwe scorer blijft deze datum zichtbaar in de noemer en blokkeert zij nieuwe publicatie wanneer zij de laatste gevraagde datum is.

De oorspronkelijke Ridge-trainjoin gaf 36.485 rijen. Binnen het 20d-rijpheidsvenster waren 2.914 herhaalde ticker/datum/strategie-sleutels zichtbaar; 140 daarvan hadden verschillende invoerwaarden. Dit zijn geen aantallen te verwijderen databasegegevens. Na toepassing van de hieronder beschreven vaste bronkeuze en koersbasiscontrole levert de werkelijk gegenereerde trainquery 33.195 kandidaatrijen op, met nul dubbele invoersleutels. Dit aantal is vóór de Python-controle op eindige features en gerijpte labels en bewijst op zichzelf geen trainbaarheid.

## Nieuwe regels vóór opslag

### Gelijke datum en zichtbare noemer

- Beide scorers koppelen uitsluitend technische data van exact de selectiedatum. De featuredatum komt expliciet mee in de invoer. Een left join houdt ook ontbrekende snapshots zichtbaar voor de dekkingscontrole.
- `model_inputs.py` controleert kalenderdatums zonder lokale middernacht naar de vorige UTC-dag te verschuiven. Toekomstige datums worden geweigerd. Automatische scoring accepteert standaard maximaal zeven kalenderdagen oude selectiedatums; het rapport vermeldt de echte datum.
- Per datum moet standaard minstens 80% van de gekozen invoerrijen complete, eindige features van diezelfde dag hebben. `MODEL_MIN_FEATURE_COVERAGE` kan een andere positieve grens tot en met 1 instellen. `MODEL_MAX_INPUT_AGE_DAYS` stelt de toegestane kalenderleeftijd in.
- Ridge vereist bovendien minimaal tien complete rijen omdat zijn cross-sectionele rangen van het cohort afhangen. Dit was eerder alleen een waarschuwing. XGBoost kan één complete ticker scoren en krijgt daarom geen kunstmatig minimum van tien tickers.
- Is de nieuwste selectiedatum onvoldoende gedekt, dan stopt scoring vóór nieuwe scoreopslag. Binnen het bestaande XGBoost-lookbackvenster kan een oudere onvoldoende gedekte datum worden overgeslagen; dat wordt per datum gelogd. Een volledige nieuwere datum blijft dan wel bruikbaar. Een onvolledige **nieuwste** datum wordt niet stil vervangen door gisteren.
- Een geaccepteerde 80%-snapshot is geen claim van 100% volledigheid. Het Ridge-bericht toont complete versus onderzochte rijen en de datum; XGBoost logt deze dekking eveneens.

### Eindige waarden, dubbels en vaste bronkeuze

Niet-numerieke waarden, NaN en oneindige waarden tellen als ontbrekend **vóór** percentielrangen worden berekend. Zo kan oneindig niet eerst een schijnbaar geldige hoogste rang worden. Ridge behoudt het bestaande principe van rangen binnen het oorspronkelijke datumcohort; onvolledige rijen verdwijnen pas na die rangberekening.

Ridge-evaluatie, training en scoring lezen per ticker/datum/strategie deterministisch de eerste bronselectie met een niet-NULL-koers, gesorteerd op `created_at` en `id`. Dat sluit aan op de standaard `DUBBELE_SELECTIE=eerste` van de forward-returnbuilder. Training/evaluatie controleren bovendien of de geselecteerde instapkoers overeenkomt met de bewaarde `forward_returns.entry_koers`, met dezelfde kleine tolerantie als de builder. Een label wordt niet aan een andere instapbasis gehangen. Volledig ontbrekende-koersgroepen blijven voor scoring wel zichtbaar, zodat ze niet ongemerkt uit de invoertelling verdwijnen.

Dit is een leesregel, geen verwijdering of herlabeling. De bestaande modelscore-sleutel kent geen afzonderlijke bronhorizon of beurs. Verschillende opgeslagen selecties kunnen daarom legitiem naast elkaar blijven bestaan terwijl het generieke model één vastgelegde vertegenwoordiger gebruikt. Wie de builder met `DUBBELE_SELECTIE=laatste` wil gebruiken, moet deze bronkeuze eveneens expliciet afstemmen; de huidige workflows gebruiken de standaard eerste rij.

Exact herhaalde modelinvoer geeft geen extra gewicht of hogere dekking. Tegenstrijdige snapshots die na de bronkeuze nog dezelfde modelinvoersleutel hebben, blokkeren de verwerking voor nader onderzoek. Ook ongeldige scalerafmetingen, featurevolgorde, niet-eindige coëfficiënten en ongeldige scores worden vóór scoreopslag geweigerd.

### Training en afhandeling van fouten

- Ridge-training gebruikt alleen gerijpte datums vóór het horizonafhankelijke kalenderembargo en minimaal 200 complete rijen over tien datums. De numerieke Ridge-parameters moeten geldig zijn vóór de model-INSERT.
- Een lege of onbruikbare hertraining retourneert nu een foutcode. De workflow start dan geen aansluitende scoring alsof hertraining geslaagd was. Het eerdere model blijft bewaard.
- De trainingsstap krijgt dezelfde Telegram-secrets voor een `ONVOLLEDIG`-melding. Bestaande scoremeldingen blijven behouden. XGBoost heeft nu ook een statusmelding voor onvolledige invoer, in plaats van alleen een bericht bij geslaagde TOP-resultaten.
- Mislukt een van de nieuwe XGBoost-voorspellingen of is een klassekans ongeldig, dan stopt de verzameling vóór `sla_scores_op`. De reeds berekende deelresultaten worden niet als een volledige nieuwe run gepubliceerd. Bestaande opgeslagen scores blijven staan.
- Expliciete historische Ridge-scoring met `--datum` behoudt de mogelijkheid een oude datum te onderzoeken. Het bericht zegt nu dat scoring met het geselecteerde model geen causale walk-forwardbacktest is. De featuredatum moet ook dan exact overeenkomen en toekomstige datums blijven verboden.

## Wat hiermee niet is bewezen

Dit zijn invoer- en publicatievoorwaarden, geen bewijs van modelkwaliteit of verwacht rendement. De bestaande Ridge-modelopslag heeft geen onafhankelijke productiepromotie op basis van out-of-sample prestaties. Historische prestaties en modeluitkomsten moeten na de opgeschoonde bronkeuze opnieuw worden geëvalueerd. Er is geen model vervangen om die evaluatie te forceren.

Een same-day snapshot bewijst evenmin dat een upstream workflow volledig is afgerond of dat iedere providerbalk een definitieve slotbalk is. De crons zijn behouden; deze wijziging beschermt de datakant, maar introduceert nog geen native workflowketen die op een geslaagde feature-/labelrun wacht. Bij een tijdelijk onvolledige laatste snapshot meldt de run daarom `ONVOLLEDIG`; een volgende uitvoering na de featurebouw kan opnieuw beoordelen.

Het parametermodel gebruikt al exacte datumjoins en een afzonderlijk beleid voor ontbrekende factoren (neutrale rangen); dat beleid is hier niet stil vervangen. Trainingen en scorers buiten de genoemde scope zijn evenmin als opnieuw volledig gevalideerd gemarkeerd.

## Verificatie

De volledige offline suite: **722 tests en 17 deeltests geslaagd**, twaalf optionele tests overgeslagen. De nieuwe tests controleren gelijke datums, ontbrekende technische data, 80%-dekking, NaN/oneindig vóór rangvorming, behoud van het oorspronkelijke rangcohort, lokale middernacht, verse/toekomstige/historische datums, identieke en tegenstrijdige snapshots, gerijpte trainingsdata, lege hertraining, geldige modelopslag, ongeldige klassekansen en afbreken vóór scorepublicatie.

De gegenereerde SQL voor beide scorers en training is daarnaast als alleen-lezen subquery op de bestaande database uitgevoerd. Canonieke Ridge-scoring voor 14 november geeft 1.484 invoerrijen en nul same-day technicals; de ontbrekende data blijft dus in de noemer. De oorspronkelijke XGBoost-lookbackquery met de nieuwe left join gaf 3.477 kandidaatrijen. Er zijn in deze controle geen scorings-/trainingsprogramma's live gestart en geen databasewrites of echte testnotificaties uitgevoerd.
