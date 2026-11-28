# Extra Trees: afzonderlijke regressieproef

De code staat in `models/10/extra_trees.py`. De module wordt met importlib
geladen omdat de mapnaam numeriek is. Dit is een onderzoeksmodel, zonder
productiepublicatie, eigen cron of echte aankopen.

ExtraTreesRegressor schat aangepast toekomstig rendement in procentpunten.
Het vaste recept bestaat uit 300 bomen, maximale diepte 8, minimaal 10
trainingswaarnemingen per blad, 80% kenmerken per splitsing, geen bootstrap,
squared_error, seed 42 en twee CPU-threads. Er wordt niet op de backtestuitslag
afgestemd. De boomgrenzen worden uitsluitend uit de trainingsinvoer geleerd;
kenmerken en targets worden niet geschaald. De elf technische kenmerken en de
vastgezette numerieke libraryversies zijn dezelfde als bij Elastic Net.

De bron is de laatst vastgelegde Elastic Net-proef voor dezelfde horizon. De
bronrun, oorspronkelijke kenmerken, koerscache en selectiecohorten worden vóór
training gekopieerd naar een eigen research_backtests-run. Een herstart behoudt
dezelfde bron. De ongewijzigde A Parameter- en Elastic Net-scores uit de bron
worden meegenomen; Extra Trees wordt per instap opnieuw getraind. Daarmee
vergelijkt de evaluator alle drie modellen, gelijkgewogen kandidaten en SPY op
dezelfde overgebleven perioden. Er zijn geen nieuwe provider-aanvragen.

Training gebruikt minimaal 100 rijen, tien selectiedatums en twintig aandelen
per datum. Alleen eindige, niet voor review gemarkeerde labels met een werkelijke
einddatum vóór de beslissing zijn toegestaan, plus een horizonafhankelijk
embargo van minimaal 25 kalenderdagen. De huidige kandidaten worden alleen op
beschikbare kenmerken beoordeeld, niet op hun toekomstige rendement of
koersbeschikbaarheid. Instellingen blijven voor alle horizons gelijk.

De top tien wordt gerangschikt op voorspeld rendement, ook als alle scores
negatief zijn. Als alle scores gelijk zijn, wordt het cohort voor alle boeken
overgeslagen met reden no_cross_sectional_signal. Onvoldoende geschiedenis en
ontbrekende gezamenlijke kandidaten worden eveneens zichtbaar gerapporteerd.
Als daardoor perioden afwijken van de oorspronkelijke Elastic Net-uitslag,
mogen die losse rendementen niet als een gematchte vergelijking worden gelezen.

Het gedeelde uitvoeringsmodel gebruikt $100.000 per portefeuille, dezelfde
niet-overlappende handelskalender, 5 bp commissie en 10 bp slippage per zijde.
Ontbrekende historische koersen laten hun vaste budget cash; andere aandelen
krijgen geen groter budget. Horizons 5/10/20/30/60 lopen afzonderlijk en één
voor één, na de bestaande ML/Elastic Net-workflows in dezelfde wachtrij.
Iedere horizon verstuurt één duurzaam geregistreerde Telegramsamenvatting.
Runs heten extra_trees_<horizon>d:<Actions-ID>.
Een ontbrekende of onverenigbare bron geeft blocked_source en een expliciete
Telegrammelding zonder rendement. Een nieuwe bron vereist vervolgens een nieuwe
Actions-run; dezelfde geblokkeerde run wordt niet stilzwijgend hergedefinieerd.

Per cohort worden alle scores, trainingshashes, cutoffdatums en boomdiagnostiek
bewaard: aantallen bomen/nodes, dieptes, structuurhash en impurity-importances.
Deze importances zijn beschrijvingen van de trainingsbomen, geen onafhankelijke
test van voorspellende waarde of oorzaak. Ze worden niet gebruikt om achteraf
kenmerken te kiezen. De configuratie bewaart ook libraryversies en de hash van
de Elastic Net-brondefinitie.

De korte historie, eventueel later gereconstrueerde kenmerken en historische
uitsluiting op koersbeschikbaarheid blijven beperkingen. Het historische
universum wordt niet opnieuw gefilterd met de nieuwe 20%-volatiliteitsgrens.
profitability_proven blijft false. Een gunstige proef promoveert dit model niet
automatisch naar productie.
