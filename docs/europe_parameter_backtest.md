# Europese parameterproef in euro

Deze afzonderlijke historische reconstructie vergelijkt het A Parameter-model
met gelijkgewogen Europese kandidaten en de iShares STOXX Europe 600 UCITS ETF
(DE), ticker EXSA.DE, ISIN DE0002635307. Die ETF volgt de STOXX Europe 600 en
noteert op Xetra in EUR. Het is een verhandelbare indexproxy met eigen fondskosten
en trackingverschillen, niet de index zelf.

De eerste uitvoering gebruikt 10 handelsdagen en €100.000 per portefeuille. De
bron is de bestaande historische parameterreconstructie: de oorspronkelijke
wereldwijde kenmerken, metadata en koerscache worden vooraf in een eigen
research_backtests-run bevroren. Succesvolle bronkoersen worden niet opnieuw
opgehaald. Alleen ontbrekende ETF- en EUR-valutareeksen worden aanvullend
vastgelegd. Een herstart behoudt bron, peildatum, instellingen en eerste
succesvolle aanvullende koersen. De bron wordt niet overschreven.

## Model en selectie

De modelinstellingen blijven gelijk: dezelfde IC-wegingen, minimumhistorie,
rollende vensters, fallbacks, top tien, grenswaarden en embargo. De training
blijft wereldwijd en gebruikt lokale-valutarendementen, zoals de bestaande
parameterreconstructie. Een trainingslabel wordt pas vrijgegeven na zijn echte
horizon-einddag; reviewlabels worden uitgesloten. De uitvoeringsportefeuille
wordt vervolgens in EUR gewaardeerd. Het model wordt dus niet ongemerkt opnieuw
getraind met een ander EUR-target of uitsluitend Europese trainingsaandelen.

De uitvoeringsselectie gebruikt de historische Europese beurscodes 041–046,
049–054 en 059 plus vooraf vastgelegde Europese tickersuffixen. Oude naamvarianten
van dezelfde beurs blijven herkenbaar via de code. Niet-ondersteunde suffixen
worden vooraf uitgesloten en geteld. Er wordt geen nieuwe Mungerfilter op de
historie toegepast en Canada is geen onderdeel van deze proef.

## Handelskalender en EUR-waardering

De handelsdagen zijn de doorsnede van de vaste ondersteunde beurskalenders
(Benelux/Ierland, Frankrijk, Duitsland, Spanje/Portugal, VK, Italië, Zweden,
Zwitserland, Polen, Noorwegen, Denemarken, Finland, Oostenrijk en Slovenië).
Alle portefeuilles handelen en waarderen op dezelfde gemeenschappelijke dagen.
Er worden geen slotkoersen over lokale feestdagen doorgetrokken. Tien zulke
dagen kunnen langer duren dan tien sessies op één beurs. De oorspronkelijke
labeldefinitie voor modeltraining blijft tien waargenomen providersessies.

Beslissing: selectiedatum plus één kalenderdag om 06:00 UTC. Instap: eerste
gemeenschappelijke sessie strikt na die UTC-datum. Uitstap: slot van de tiende
gemeenschappelijke sessie. Mandjes overlappen niet. Deze datums kunnen afwijken
van Amerikaanse backtests; vergelijk Europese modellen en benchmark binnen
dezelfde proef, niet zomaar percentages tussen regio's of horizons.

Yahoo auto_adjust=True blijft de historische total-returnproxy. Dividenden en
splitsingen worden niet nogmaals opgeteld. EUR-koersen blijven ongewijzigd.
GBp/GBX wordt eerst door 100 gedeeld; GBP, CHF, SEK, NOK, DKK, PLN, USD en CAD
worden met hun directe historische EUR-kruis omgerekend indien nodig. De
ETF noteert zelf in EUR en krijgt geen tweede FX-omrekening.

Voor opening en slot worden de corresponderende dagelijkse FX-open/close
gebruikt. Aandelen en FX-dagkoersen zijn niet exact tijdgesynchroniseerd en
vormen geen bewijs van destijds uitvoerbare transacties. Ontbrekende FX-dagen
worden niet voorwaarts gevuld. Een geheel ontbrekende benchmark/FX-reeks blokkeert
de berekening totdat de invoer is aangevuld. Ontbrekende individuele aandelen-
of omrekenkoersen laten hun vaste plaats cash; de overige posities worden niet
vergroot en er worden geen vervangers gekozen.

Basiskosten zijn 5 bp commissie en 10 bp slippage per zijde, net als in de
Amerikaanse proef. Voor niet-EUR-noteringen komt daar 5 bp FX-kosten per zijde
bij als expliciete onderzoeksaanname, geen actuele brokerprijs. Er zijn geen
dagelijkse omwisselkosten op tussentijdse waarderingen. De benchmark krijgt
dezelfde basiskosten en in-/uitstapdatums. De gelijkgewogen baseline heeft
dezelfde kandidaten als het model, maar verdeelt over alle kandidaten.

## Betrouwbaarheid en rapportage

De kalender-, conversie- en uitvoeringsdefinitie, modelhash en libraryversies
staan in de configuratie. Alle rangschikkingen en IC-gewichten worden vóór de
uitvoeringsevaluatie opgeslagen. Resultaten en voortgang staan uitsluitend in
private onderzoekstabellen; productie, bestaande USD-paperproeven en hun
vastgezette protocol blijven gelijk.

De aparte workflow meldt rendement inclusief cash, eindwaarde, terugval,
werkelijke beleggingsperiode en overgeslagen aandelen in EUR via de bestaande
duurzame Telegramverzending. De claim wordt vóór verzending opgeslagen; een
onzekere bezorging wordt niet automatisch verdubbeld. Open perioden en
onvolledige gegevens blijven expliciet zichtbaar.

De historische kenmerken kunnen later gereconstrueerd zijn. De korte periode,
providerherzieningen, delistings en uitsluiting op achteraf bekende
koersbeschikbaarheid kunnen vertekenen. De brede indexproxy kan andere landen-
en sectorgewichten hebben dan het strategie-universum; outperformance is dus
niet automatisch zuivere selectiewaarde. profitability_proven blijft false.
