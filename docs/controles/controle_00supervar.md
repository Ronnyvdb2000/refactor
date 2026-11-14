# Controle Superbot — 13 november 2026

## Herstelde fouten

De EU- en Noord-Amerikaworkflows geven nu SUPABASE_DB_URL door en installeren
psycopg2-binary en python-dotenv. Database-opslag en e-mailverzending worden
gecontroleerd. Telegram blijft ongeconfigureerd. Loguitvoer wordt direct getoond.
De workflow heeft expliciet alleen leestoegang tot repository-inhoud.

Elke ticker vereist minstens 253 geldige afgeronde koersdagen. Dat geeft
252 rendementintervallen voor de jaarvergelijking. De bot controleert numerieke,
positieve en samenhangende OHLC-prijzen, eindige niet-negatieve volumes,
dubbele datums, ontbrekende tickers en recent handelsvolume. Koersdagen van
vandaag en toekomstige datums worden uitgesloten. De laatste geldige koersdag
mag maximaal zeven kalenderdagen oud zijn. De gemeenschappelijke downloader
behoudt zijn afzonderlijke herpoging bij ontbrekende batchdata; daarna worden
onbruikbare histories zichtbaar overgeslagen.

De eigen RS-score behoudt de bestaande gewichten: 40% rendement over 63 sessies,
60% over 252 sessies. Gelijke prestaties krijgen dezelfde rang. De schaal loopt
nu van 1 tot 99 bij verschillende scores; een enkele ticker krijgt neutraal 50.
De vergelijkingsgroep is het beschikbare universum binnen de gekozen regio,
niet een officiële marktbrede RS-rating. Weggevallen tickers kunnen de rang
van andere tickers veranderen.

Indicatoren worden berekend op chronologische histories. De 52-weeksgrenzen
gebruiken High en Low. Het volume wordt vergeleken met het gemiddelde van de
20 voorgaande sessies, zonder de huidige signaaldag. De pivot is de hoogste
High van de 20 voorgaande sessies. De uitbraakdag telt niet mee in die grens.
De stop combineert de Low van datzelfde voorafgaande venster met de bestaande
8%-grens; stops op of boven de actuele koers worden geweerd.

Scores, koersen en stops blijven ongerond tot de presentatie. Gelijke totaalscores
worden deterministisch op ticker gesorteerd. De dagelijkse rapportage toont
unieke datadekking, ontbrekende lijsten, analysefouten, kandidaten per beurs en
opgeslagen selecties. Geen kandidaten en geen bruikbare data zijn afzonderlijke
uitkomsten. Analysefouten, geheel ontbrekende bruikbare data, opslagfouten en
e-mailfouten laten de run mislukken. Gedeeltelijke data geeft een gemarkeerd
[ONVOLLEDIG]-rapport, ook als de resterende analyses succesvol waren.

## Presentatie en modelgrenzen

De bot verzint geen eurobedragen of positiegrootte meer voor buitenlandse
noteringen. Zonder per-ticker valutaomrekening is het eerdere aandelenadvies
op een fictieve portefeuille van EUR 50.000 niet onderbouwd. Koers, entry,
pivot en stop worden als oorspronkelijke koerseenheden getoond. De eerdere
R/R-verhouding is verwijderd: een pivot is een uitbraakreferentie, geen koersdoel.
Een referentie-entry is geen gegarandeerde uitvoeringsprijs.

De bestaande vierdelige VCP-heuristiek, minimumscore 2/4, trendvoorwaarden,
volatiliteitsfilter op of boven de beursmediaan en één winnaar per beurs blijven
behouden. Droogvallend volume en een volume-uitbraak zijn verschillende
voorwaarden; een score 2/4 betekent geen bevestigde uitbraak. Er is geen
backtest of onafhankelijke verificatie van de koersleverancier uitgevoerd.
Een datum vóór vandaag kan bij een intraday herstart bewust de meest recente
afgesloten lokale sessie overslaan; dit is een conservatieve daggrens.
De 7-dagengrens is een eenvoudige versheidscontrole, geen beurskalender.

## Validatie

27 offline regressietests slagen voor OHLCV-validatie, ontbrekende en verouderde
data, tijdzones, toekomstige en lopende sessies, rendementintervallen,
RS-ties, hoogste/laagste koersen, de eerdere volumebaseline, pivot en breakout,
stopvalidatie, precisie, presentatie en foutafhandeling van analyses/opslag/e-mail.

`python -m unittest discover -s tests -p 'test_00supervar.py'`

Geen echte meldingen verstuurd en geen database gewijzigd tijdens de tests.
Een live hertest via run_00supervar.yml op main staat nog open. Handmatig starten
voert zowel Europa als Noord-Amerika uit. Controleer beide jobs, databereik,
opslagaantallen en de ontvangen e-mails.
