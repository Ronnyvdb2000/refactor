# Gematchte portefeuillebacktest van ML-modellen

Deze aparte proef vergelijkt XGBoost V3, LightGBM V2, CatBoost V1 en het
A Parameter-model op dezelfde historische instapdatums, kandidaat-aandelen,
uitvoeringsprijzen en kosten. Iedere portefeuille begint met $100.000 en kiest
maximaal tien aandelen. Ontbrekende koersen laten de oorspronkelijke plaats
cash; er wordt geen groter budget aan de andere aandelen gegeven. SPY en een
gelijkgewogen kandidatenportefeuille krijgen dezelfde instap- en uitstapdatums.

## Onderzoeksvarianten

De proef laadt geen bestaande productiemodellen: die kunnen op latere gegevens
getraind zijn. Bij ieder instapmoment worden de drie classifiers opnieuw getraind
met hun vaste kandidaatinstellingen:

| Model | Instellingen uit de bestaande kandidaatcode | Onderzoeksdoel |
| --- | --- | --- |
| XGBoost V3 | 150 bomen, learning rate 0,03, diepte 5, subsample/colsample 0,8, seed 42; CPU hist | Positief aangepast rendement |
| LightGBM V2 gecontroleerd | 400 bomen, learning rate 0,03, 15 bladeren, diepte 5, seed 42 | Positief aangepast rendement |
| CatBoost V1 | 400 iteraties, learning rate 0,03, diepte 5, L2 5, seed 42 | Positief aangepast rendement |

LightGBM en CatBoost ondersteunen ook een relatief/excess-target in hun gewone
kandidaatproeven. Hier is bewust voor alle drie hetzelfde absolute target gekozen.
Dit zijn dus opnieuw getrainde onderzoeksvarianten, geen nagespeelde voorspellingen
van de huidige productie-artifacts. Er wordt niet geoptimaliseerd op deze uitslag.

De ML-invoer bestaat uit de elf bestaande technische kenmerken: atr14,
atr14_pct, rsi14, ibs, ma50, ma200, pct_from_ma50, pct_from_ma200,
vol_ratio_20d, high52w en pct_from_high52w. Het A Parameter-model behoudt zijn
eigen factoren en bevroren historische scores, maar wordt opnieuw gerangschikt
binnen hetzelfde kandidaatuniversum met volledige ML-invoer. Zijn rendement kan
daarom afwijken van de eerdere zelfstandige parameter-backtest. De originele
uitslag blijft bewaard.

## Grens tussen training en test

De bron is de eerder vastgelegde parameter-backtest voor dezelfde horizon.
Kenmerken, koersen en oorspronkelijke handelskalender worden gekopieerd naar
een nieuwe `research_backtests`-run voordat training begint. De bron wordt niet
aangepast en er wordt geen nieuwe koershistorie opgehaald.

Trainingsreturns en hun einddatums worden gereconstrueerd voor 5, 10, 20, 30 of
60 werkelijk door de provider gerapporteerde sessies. Training gebruikt uitsluitend
labels met een einddatum strikt vóór de beslissing en selectiedatums uiterlijk
op de grens van het horizonafhankelijke embargo, met minimaal 25 kalenderdagen.
Er zijn minimaal 100 rijen, tien trainingsdatums, twintig aandelen per trainingsdatum
en beide targetklassen nodig. Het trainingsvenster groeit met de beschikbare
historie. Testuitkomsten worden niet aan `fit` meegegeven en er is geen early stopping
op de buitenste testperiode.

Bij de selectie tellen alleen toen beschikbare technische kenmerken en de
oorspronkelijke US-kandidaten. Een toekomstige return of toekomstige
koersbeschikbaarheid bepaalt niet wie gescoord wordt. Cohorten met onvoldoende
bekende training of minder dan tien gezamenlijke kandidaten worden voor alle
modellen overgeslagen en apart geregistreerd. De werkelijk gebruikte
beleggingsperiode en het aantal perioden staan in het resultaat.

Iedere bevroren cohort bewaart alle scores en rangschikkingen, gebruikte
trainingsdatums, laatste bekende labeldatum, embargo, features en hashes van
trainingsinvoer en scores. Modelrecepten, versies van de drie libraries, broncodehash
en verwijzing naar de bronrun worden eveneens opgeslagen.

## Uitvoering en rapportage

Workflow: `Backtest ML-modellen - gematchte historische vergelijking`.
Alle vijf horizons lopen afzonderlijk en na elkaar; een fout annuleert de andere
horizons niet. Runs heten `ml_compare_10d:<Actions-ID>` en overeenkomstig voor
de overige horizons. Iedere horizon krijgt één Telegramsamenvatting met alle
modellen, benchmarks, geïnvesteerde bedragen en cash. De bestaande private
verzendregistratie voorkomt dat dezelfde bevestigde melding dubbel wordt gestuurd.

De simulatie en kostenaannames zijn dezelfde als bij de parameter-backtest:
eerste beurssessie na de beslissingsdatum, uitstap op de horizon-einddag,
niet-overlappende mandjes, 5 basispunten commissie en 10 basispunten nadelige
koersafwijking per zijde. Een laatste open periode blijft tussentijds.

De korte historie, mogelijk later gereconstrueerde kenmerken, absolute prijsniveaus
als features en ontbrekende/delisted koersen beperken de interpretatie.
Historische uitsluiting op onvolledige uitvoeringskoersen kan vertekenen en wordt
gemeld. Meer modellen en horizons proberen vergroot de kans op een toevallige
winnaar. Er volgt geen automatische productiepromotie; de lopende paperproeven,
productiemodelbestanden, selecties en scorearchieven worden niet gewijzigd.
