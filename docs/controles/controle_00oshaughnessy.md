# Controle bot_00oshaughnessy — 13 november 2026

De bestaande selectie blijft een eigen VC2-variant met zeven factoren: P/E, P/B,
P/S, P/CF, EV/EBITDA, EV/FCF en shareholder yield. Het rapport noemt dit expliciet;
het is geen exacte reproductie van een officiële O'Shaughnessy-strategie.

## Herstel

- Elke unieke ticker wordt eenmaal opgehaald, ook bij overlap tussen beurzen.
- De marktkapitalisatiegrens van 200 miljoen geldt in USD, met een recente
  wisselkoers. Ontbrekende wisselkoersen geven een zichtbare oversla-redenen.
- Koers- en verslagvaluta moeten vergelijkbaar zijn. GBp/GBX worden voor
  bedrijfswaardering als GBP herkend; de weergegeven koers houdt de koerseenheid.
  Verschillende valuta worden overgeslagen, niet stilzwijgend gecombineerd.
- Jaarlijkse balans-, resultaat- en cashflowgegevens gebruiken hetzelfde
  gemeenschappelijke boekjaar, maximaal 550 dagen oud en niet toekomstig.
- Ontbrekende schuld, cash, dividenden en aandelenuitgifte worden niet nul.
  Shareholder yield is (cashdividenden minus netto aandelenuitgifte) gedeeld door
  actuele marktkapitalisatie. Expliciete nul en negatieve yield blijven geldig.
- Minimaal vijf werkelijk beschikbare factoren zijn vereist voordat de
  percentielpopulatie wordt berekend. Gelijke waarden krijgen gelijke rangen.
- De hoogste 10% samengestelde scores gaat door naar momentum; gelijke scores
  op de grens kunnen de groep vergroten. Er is geen aanvulling van buiten die groep.
- Momentum vergelijkt aangepaste slotkoersen met een koers op of vlak voor het
  kalenderanker van zes maanden. Beide eindpunten mogen maximaal zeven dagen
  achterlopen. Korte IPO-historie, toekomstige en ongeldige prijzen worden geweerd.
- Ranking bewaart volledige precisie; afronding gebeurt uitsluitend in de output.
- Rapporten tonen datadekking, oversla-redenen, ontbrekend momentum, lege lijsten
  en database-opslag. E-mailfouten, programmeerfouten, mislukte opslag en een
  geheel onbruikbaar data-universum leveren een mislukte run op.
- De workflow laat Telegram uit en toont logregels onmiddellijk.

## Grenzen

De score combineert actuele/TTM-providerverhoudingen met jaarlijkse cashflowratio's.
De zevende factor en het gemiddelde over beschikbare factoren zijn eigen
modelkeuzes. Dit is geen historisch gevalideerde reproductie van VC2. Momentum
is rendement in de lokale koerseenheid; er is geen valutaomrekening van rendement.
Meervoudige noteringen van dezelfde onderneming worden niet op ondernemingsniveau
samengevoegd. Een uitvoerbare selectie valideert de aangeleverde jaarcijfers niet
onafhankelijk. De bot ondersteunt geen backtest.

OSAM beschrijft eigen samengestelde waarderingsfactoren en shareholder yield in:
https://canvas.osam.com/Commentary/BlogPost?Permalink=value-momentum-building-a-unique-canadian-equity-portfolio

## Validatie

28 offline regressietests: cashratio's en dividend-/uitgifte-eenheden,
ontbrekende versus expliciete nulwaarden, gedeeld boekjaar, verslagleeftijd,
valuta en USD-grens, percentielpopulatie, ties, deduplicatie, momentumanker,
tijdzones, verouderde/toekomstige koersen en afhandeling van opslag/verzending.
Geen echte berichten verstuurd en geen database gewijzigd tijdens deze tests.

Uitvoeren: `python -m unittest discover -s tests -p 'test_00oshaughnessy.py'`.
Live hertest nog uit te voeren via `run_00oshaughnessy.yml` op main. Controleer
het eindrapport, aantallen overgeslagen tickers, databasebevestiging en e-mail.
