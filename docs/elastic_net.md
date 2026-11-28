# Elastic Net: afzonderlijke regressieproef

Deze proef schat het aangepaste toekomstige rendement in procentpunten. Het is
een echte ElasticNet-regressor, geen classifier met Elastic-Net-straf. Het
model gebruikt dezelfde elf technische kenmerken als de bestaande gematchte
ML-vergelijking. Fundamentals toevoegen zou ook de invoer veranderen en is geen
onderdeel van deze eerste proef.

Vast recept: alpha 0,01, l1_ratio 0,5, maximaal 50.000 iteraties, tolerantie
0,000001 en cyclische coördinaatupdates. X en y worden uitsluitend op de
trainingsgegevens gestandaardiseerd. Voorspellingen worden terugvertaald naar
rendementsprocentpunten. Er is geen zoekprocedure op de testuitslag en geen
willekeurige verdeling van aandelenrijen over training en test. Een
convergentieprobleem wordt een fout, geen stilzwijgend geaccepteerd model.

De bron is de laatst vastgelegde gematchte ML-vergelijking voor dezelfde horizon.
De bronrun, oorspronkelijke kenmerken, koersen en kalender worden bevroren in
een eigen research_backtests-run. Een herstart behoudt dezelfde bronrun. Er
worden geen nieuwe Yahoo-aanvragen uitgevoerd en geen historische fundamentals
verzonnen. De historie blijft het oorspronkelijk geselecteerde US-universum;
deze reconstructie test dus niet de nieuwe volatiliteitsgrens van 20%.

Iedere instap traint opnieuw met minimaal 100 rijen, tien trainingsdatums en
twintig aandelen per trainingsdatum. Alleen eindige, niet ter review gemarkeerde
returns waarvan de werkelijke einddatum vóór de beslissing ligt tellen mee.
Het horizonafhankelijke embargo is minimaal 25 kalenderdagen. De training ziet
geen toekomstige returns of latere koersbeschikbaarheid van de kandidaten.
Verschillende rendementen van dezelfde richting zijn voldoende voor regressie;
er zijn geen twee classificatieklassen vereist.

De top tien wordt op voorspeld rendement gerangschikt, ook als alle voorspelde
rendementen negatief zijn: er wordt geen nieuwe markt-timingregel toegevoegd.
Wanneer alle scores gelijk zijn, wordt het cohort voor alle boeken overgeslagen
met reden no_cross_sectional_signal; er volgt geen alfabetische top tien.
Overgeslagen cohorten staan expliciet in het resultaat, waardoor de periode kan
afwijken van de oorspronkelijke ML-uitslag. Vergelijk die losse cijfers dan niet
zonder gelijke perioden. A Parameter wordt hier op dezelfde kandidaten en
overgebleven perioden berekend, naast SPY en gelijkgewogen kandidaten.

Iedere portefeuille begint met $100.000. De bestaande evaluator verzorgt vaste
cashplaatsen bij ontbrekende historische koersen, niet-overlappende perioden,
5 bp commissie en 10 bp slippage per zijde. Beschikbare horizons zijn
5/10/20/30/60 handelsdagen. Ze lopen één voor één; een fout annuleert de volgende
horizon niet. Per horizon volgt een eigen Telegrambericht. De eigen runsleutel
is elastic_net_<horizon>d:<Actions-ID>.

Scores, trainingshashes, datumgrenzen, scalerwaarden, coëfficiënten en solver-
diagnostiek worden per cohort bewaard. De coëfficiënten zijn procentpunten
rendement per trainingsstandaardafwijking van het betreffende kenmerk; bij sterk
gecorreleerde kenmerken zijn ze geen bewijs van economisch oorzakelijk verband.
De libraryversie en codehash worden eveneens opgeslagen.

De korte historie, eventueel later gereconstrueerde kenmerken en historische
uitsluiting op koersbeschikbaarheid blijven beperkingen. profitability_proven
blijft false. Er is geen automatische publicatie als productiemodel. Extra
Trees volgt als afzonderlijke volgende proef.
