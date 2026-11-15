# Weekly livecontrole en brononderzoek — 15 november 2026

## Volledige a-run na ef50239

[Run 43678418052](https://github.com/Ronnyvdb2000/refactor/actions/runs/43678418052) startte op 15 november om 04:49:24 UTC (05:49:24 Belgische tijd). De checkout was main `2dac9d96ebefb6890be3dd2974cdba1c47d19ef0`, waarvan ef50239 de directe ouder is; de enige tussenliggende wijziging betreft XGBoost-modellen. Dit is dus een echte run met de weekly-herstelcode van ef50239.

De gehele workflow duurde ongeveer twee minuten. De scan liep van 04:49:43 tot 04:51:17 UTC, circa 1m34s. Van 14163 unieke invoersymbolen werden zestien batches van honderd volledig afgehandeld. Daarvan hadden 1447 symbolen koershistorie; dit is **geen** meting van 1447 geldige weekrendementen. In batch 17 (1601–1700) werd rate-limiting gemeld. Na de ene herstelpauze en batchretry bleef die beperking aanwezig. De scan stopte met `DownloadOnderbroken`.

| Controle | Waarneming |
| --- | --- |
| Imports en modulepaden | Geslaagd |
| Vier workers en maximaal één batchretry | Zichtbaar in code en batchlog |
| Stop na herhaalde rate-limit | Geslaagd; geen duizenden losse retries |
| Nieuwe toppers-JSON opslaan | Stap overgeslagen |
| Automatische winnaarsanalyse | [Run 43678560095](https://github.com/Ronnyvdb2000/refactor/actions/runs/43678560095) overgeslagen |
| Rapportartifact | Geüpload, alleen de foutmelding; 263 bytes ZIP |
| Telegram | ONVOLLEDIG-melding aangeroepen, geen verzendwaarschuwing; ontvangst niet onafhankelijk bevestigd |
| Volledige scandekking | Niet vastgesteld; providerstop vóór afronding |

De eerdere groene run van 36m51s mag niet als vergelijkbare succesvolle scan worden gebruikt: die publiceerde ondanks onvoldoende dekking. De nieuwe rode run toont dat de blokkering werkt, niet dat het hele universum betrouwbaar is gescand.

## Aanvullende correctie: diagnose bij een providerstop

Voorheen ging de dekking uit reeds afgehandelde batches verloren zodra een latere batch afbrak. A/x en opvolg bewaren nu ook bij een providerstop een CSV voor alle invoersymbolen. Er worden daarvoor geen nieuwe verzoeken gedaan.

| status | Betekenis | bruikbaar |
| --- | --- | --- |
| bruikbaar | Cache bevat een geldig weekrendement | True |
| geen_bruikbare_weekdata | Download afgehandeld, maar weekdata ontbreekt of voldoet niet | False |
| download_afgebroken | Symbool zat in de batch die afbrak; uitkomst onbekend | Leeg |
| niet_opgevraagd | Download voor dit symbool nog niet gestart | Leeg |

De laatste twee categorieën mogen niet als bewezen verkeerde bronsymbolen worden geïnterpreteerd. De snapshots en export blijven geblokkeerd; Telegram blijft behouden. Limieten op workers en retries zijn ongewijzigd.

## Bronsymbolen Warschau

De bronlijst gebruikt veel GPW-weergavenamen met `.WA`, terwijl Yahoo de handelscode nodig heeft. Voorbeeld: `CDPROJEKT.WA` wordt `CDR.WA`. De [issuerinformatie van CD PROJEKT](https://www.cdprojekt.com/en/investors/basic-data/) bevestigt CDR/CDR.WA.

Controleprocedure: de [volledige officiële GPW-bedrijvenlijst](https://www.gpw.pl/spolki?offset=0&limit=500) levert het ISIN en de handelscode. Op de bijbehorende bedrijfskaart worden de verkorte weergavenaam, hetzelfde ISIN en de handelscode gecontroleerd. Alleen een exacte overeenkomst met een bestaande broninvoer krijgt een alias. Geen fuzzy matching en geen automatische koppeling naar een Amerikaanse of andere buitenlandse notering. Het register `weekly/symbols.csv` bewaart het bewijs per koppeling.

Een ontbrekende exacte match is geen bewijs van delisting. Die broninvoer blijft staan en blijft meetellen bij de dekkingsnorm. De audit-CSV naast dit document maakt het onderscheid zichtbaar.

Alle 402 bedrijfskaarten uit de GPW-lijst zijn opgehaald en gecontroleerd, zonder ontbrekende of inconsistente identiteit. Van de 361 a-bronsymbolen krijgen 329 een geverifieerde handelscode; 25 waren al correct en zeven hebben geen exacte actuele GPW-match: CAPITAL.WA, FMG.WA, IBSM.WA, LESS.WA, PGFGROUP.WA, PURE.WA en SHOPER.WA. Deze zeven worden niet verwijderd. Alle 29 x-symbolen komen overeen met actuele GPW-handelscodes. De noemer van Warschau blijft 361; er is geen dekking gewonnen door moeilijke symbolen weg te laten.

De [audit-CSV](weekly_symbolen_20261115.csv) bevat 590 oorspronkelijke a/x-invoerregels voor beide beurzen, met de weekly-code en identificatiestatus. Dit is een **identiteitsaudit**, geen CSV met gemeten weekkoersdekking. Het runtime-register bevat 329 Warschau- en vijf Helsinki-koppelingen. Helsinki-a gaat van 188 bronregels naar 187 unieke noteringen doordat KONE.HE en de reeds aanwezige KNEBV.HE hetzelfde beursgenoteerde B-aandeel betreffen.

## Bronsymbolen Helsinki

| Bron | Vaststelling | Actie |
| --- | --- | --- |
| KCR1V.HE | [Nasdaq: code sinds 5 januari 2017 KCR](https://view.news.eu.nasdaq.com/view?id=b4cb824e10b4d2bd096e3fd4b26e5c267&lang=en) | KCR.HE |
| KONE.HE | [KONE: uitsluitend B-aandelen beursgenoteerd; code KNEBV, ISIN FI0009013403](https://www.kone.com/en/Images/KONE_Annual%20Review_2025_tcm17-141619.pdf) | KNEBV.HE; bestaande dubbele invoer samenvoegen |
| TELIA.HE | [Nasdaq: Helsinki-code TELIA1](https://view.news.eu.nasdaq.com/view?id=b040a5f40fb38be8470bdccc1691e40c5&lang=en) | TELIA1.HE |
| VAISALA.HE | [Vaisala: A-aandeel VAIAS, ISIN FI0009900682](https://www.vaisala.com/en/investors/shares-and-shareholders) | VAIAS.HE |
| GLASTON.HE | [Glaston: code GLA1V, ISIN FI4000587340](https://view.news.eu.nasdaq.com/view?id=b66d676dd34c492ca2f58b3bc2555cb0e&lang=en) | GLA1V.HE |
| ROVIO.HE | [Nasdaq: laatste notering 22 januari 2024](https://view.news.eu.nasdaq.com/view?id=b904f5e8b8c3ec587b8e19ae6944f5abd&lang=en) | Geen vervanging; bron niet stilzwijgend verwijderen |
| CRAMO.HE | [Nasdaq: Cramo werd op 3 juni 2020 verwijderd; oude code CRA1V](https://view.news.eu.nasdaq.com/view?id=b85c41499cdbf8f85dc94f0a1f04f8b31&lang=en) | Geen actuele vervanging afleiden uit de naam |
| AMER.HE | [Nasdaq: oude Amer Sports-notering AMEAS eindigde op 4 september 2019](https://view.news.eu.nasdaq.com/view?id=b804d9b11501c8a2b8017a8a1e4ba787c&lang=en) | Geen automatische koppeling naar een andere beurs |
| 캐피탈.HE | Bron bevat deze tekst; identiteit niet vastgesteld | Ongewijzigd en zichtbaar houden |

De overige Helsinki-invoer is hiermee niet volledig geverifieerd. Zonder eenduidige issuer-/aandelenklasse-identiteit wordt geen alias verzonnen. Het register vervangt geen complete actuele beurslijst.

## Liveverificatie en beperkingen

Een kleine controle van zeven ruwe/vervangende symbolen gaf weekhistorie voor CDR.WA, KCR.HE en KNEBV.HE (laatste koersdatum 13 november 2026). CDPROJEKT.WA, KCR1V.HE, KONE.HE en de Koreaanse invoer gaven in die batch geen historie. Geen rate-limitmelding in deze kleine controle.

Een afzonderlijke poging om alle 361 Warschau- en 188 Helsinki-bronsymbolen te meten is daarna in de eerste batch afgebroken wegens herhaalde rate-limiting. De gedeeltelijke batchresultaten zijn niet gebruikt als bewijs dat ontbrekende symbolen ongeldig zijn. Er is dus geen betrouwbaar nieuw volledig dekkingspercentage. Geen echte notificaties of database-export tijdens deze lokale controles.

De volledige Actions-run hierboven is uitgevoerd vóór de aanvullende alias- en diagnosewijzigingen van dit document. Een officiële handelscode valideert de identiteit, maar garandeert geen bruikbare Yahoo-weekdata. De 80%-norm blijft actief; een onvolledig rapport wordt niet gepubliceerd.

### Volledige lokale a-controle met de aanvullende correcties

Daarna is de echte rapportfunctie opnieuw gestart voor het volledige a-universum, met de definitieve 334 koppelingen en dezelfde week 9–13 november. Alle bronbestanden en de bestaande JSON waren naar een aparte controledirectory gekopieerd; alleen de verzendfuncties waren voor deze lokale controle vervangen, zonder externe verzending. Productiecode en workflow-notificaties blijven behouden.

Start 05:31:43 UTC (06:31:43 Belgische tijd), einde 05:32:58 UTC: circa **1m14s**. Het universum bevatte 14162 unieke symbolen. De eerste batch meldde rate-limiting, met 32 koersreeksen bij poging 1 en 46 bij poging 2. Na de voorgeschreven ene herstelpauze bleef de beperking bestaan; de scan is gestopt. Deze deelreeksen zijn niet gebruikt als betrouwbare afgeronde batch.

De nieuwe diagnose is daadwerkelijk aangemaakt: 14394 invoerregels over alle a-lijsten, met 100 regels `download_afgebroken` en 14294 regels `niet_opgevraagd`. Na ontdubbelen zijn dat 100 respectievelijk 14062 unieke symbolen. `bruikbaar` bleef voor alle onbekende uitkomsten leeg. De bestaande toppers-JSON bleef byte voor byte ongewijzigd. Er is geen ranglijst gepubliceerd en geen database-export uitgevoerd.

De lokale omgeving en GitHub-runner zijn verschillende uitvoeromgevingen; het eerdere stoppunt in de lokale controle is daarom geen vergelijking van hun snelheid of quota. Beide runs bevestigen de actieve providerbeperking. Een volledig geslaagde live-scan en nieuwe weekdekkingspercentages blijven onbevestigd; de correcties worden niet gepresenteerd als garantie dat alle Yahoo-data beschikbaar is.

## Offlineverificatie

`python -m pytest -q tests/`: **615 tests geslaagd**, 9 overgeslagen sockettests en 17 geslaagde subtests. De vier aanvullende tests controleren cachebehoud en onbekende statussen bij een providerstop, geen nieuwe diagnose-downloads of export, behoud van onbekende symbolen in de dekkingsnoemer, samenvoegen van dezelfde notering en een bronregister zonder conflicten of aliasketens. RUFF (`F401,F821`) en `git diff --check` geslaagd. Bronbestanden, YAML-notificaties en refactor-lab zijn niet gewijzigd.
