# Europese ML-vergelijking op de bevroren EUR-proef

Deze aparte 10-daagse proef vergelijkt A Parameter, XGBoost V3, LightGBM V2,
CatBoost V1, Elastic Net en Extra Trees. Iedere portefeuille begint met €100.000,
naast STOXX Europe 600 ETF EXSA.DE en gelijkgewogen kandidaten. De bron is de
afgeronde berekening van de Europese parameterproef, inclusief een eventueel
nog open laatste periode. De bronselecties, kenmerken, oorspronkelijke aandelen-
koersen, benchmark en FX worden gekopieerd en bevroren vóór training. Er zijn
geen nieuwe koersaanvragen. Een herstart behoudt dezelfde bronrun.

Alle boeken krijgen dezelfde kandidaten met de elf bestaande volledige technische
kenmerken, dezelfde Europese kalender, instap/uitstapdatums, EUR-conversie,
pencecorrectie, basiskosten en extra FX-kosten. Ontbrekende uitvoeringskoersen
laten vaste plaatsen cash; andere aandelen krijgen geen extra budget. De
parameter-scores blijven die uit de bron, maar worden opnieuw gerangschikt binnen
het gemeenschappelijke kandidatenuniversum. De eerdere +6,61%-uitslag wordt
niet overschreven en mag niet zonder controle van kandidaten en perioden naast
de nieuwe ML-uitslagen worden gezet.

De vijf ML-modellen worden per instap opnieuw op Europese trainingsrijen
getraind. A Parameter behoudt zijn oorspronkelijke wereldwijde IC-wegingen en
eigen factoren. Dit vergelijkt de bestaande onderzoeksaanpakken, niet identieke
modelinvoer of trainingsuniversa voor alle algoritmen. ML gebruikt minimaal 100
rijen, tien trainingsdatums en twintig complete aandelen per trainingsdatum.
Labels moeten niet ter review staan en hun werkelijke einddatum moet vóór de
beslissing liggen. Het kalenderembargo is horizonafhankelijk, minimaal 25 dagen.

XGBoost, LightGBM en CatBoost classificeren positief lokaal aangepast rendement;
Elastic Net en Extra Trees schatten het lokale rendement in procentpunten.
EUR-waardering geldt voor de uitvoering. De recepten blijven die uit de eerdere
Amerikaanse proeven. XGBoost 3.2.0, LightGBM 4.7.0 en CatBoost 1.2.8 sluiten aan
bij de daar geregistreerde versies; scikit-learn en numerieke afhankelijkheden
zijn eveneens vastgezet. Elastic Net schaalt alleen met trainingsgegevens.
Er is geen afstemming op de uitkomst van deze proef.

Alle modellen krijgen voldoende gemeenschappelijke training en kandidaten, of
het hele instapmoment wordt zichtbaar overgeslagen. Bij een vlakke score van
een model wordt evenmin een alfabetische top tien verzonnen. Die beslissing
gebruikt geen toekomstige uitkomst. Overgeslagen cohorten kunnen wel maken dat
de nieuwe meetperiode van de bron afwijkt; dat blijft expliciet in de rapportage.
Een trainingsfout levert een foutstatus zonder gefabriceerd rendement.

Trainingshashes omvatten zowel de continue returns als de classificatietargets.
Scores, rangschikkingen, modeldiagnostiek, datumgrenzen en libraryversies worden
opgeslagen. De complete selectie wordt gecommit vóór EUR-uitvoeringsevaluatie.
Een herstart van een onderbroken evaluatie hergebruikt die bevroren selectie.
BLAS gebruikt één thread om onnodige CPU-oversubscriptie te voorkomen; de
boomrecepten behouden hun twee workers. Dat wijzigt geen hyperparameters.

De afzonderlijke workflow stuurt één compacte acht-portefeuille-samenvatting
naar Telegram via dezelfde duurzame verzendclaims. De simulatiestap ontvangt
geen Telegramsecrets. Het Europese USD-onafhankelijke uitvoeringsmodel en de
oorspronkelijke parameterproef blijven ongewijzigd.

De korte historie, later gereconstrueerde kenmerken, absolute prijsniveaus,
verschillende leerdoelen, FX-dagkoersproxies en historische koersuitsluiting
blijven beperkingen. Meerdere modellen proberen vergroot de kans op een
toevallige winnaar. profitability_proven blijft false; productie en lopende
paperproeven worden niet aangepast.
