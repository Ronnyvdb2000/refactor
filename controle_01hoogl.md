# Controle Hoogland GARP — 14 november 2026

Repository uitsluitend Ronnyvdb2000/refactor; refactor-lab apart.
Script bot_01hoogl.py, workflows run_01hoogl.yml en run_01hoogl_full.yml.
Dit is de GARP-bot, niet weekly_rep_backtest.py dat berichten met de lijstnaam
'01 Hoogland' verstuurde. Die wekelijkse Telegram-verzending is eerder afzonderlijk
uitgeschakeld in commit c79482e.

## Hersteld

- Telegram-import, aanroepen en onjuiste verzendmeldingen verwijderd, ook bij
  rate limiting. Beide workflows houden Telegram ongeconfigureerd. E-mail en
  database met bestaande strategie-identificaties blijven behouden.
- Niet-eindige cijfers kunnen geen punten krijgen. Ongeldige currentPrice valt
  terug op een geldige regularMarketPrice; zonder positieve koers of koerseenheid
  wordt een ticker zichtbaar overgeslagen. Als een koersdatum beschikbaar is,
  mag hij niet toekomstig of meer dan zeven kalenderdagen oud zijn. Bij ontbrekende
  koersdatum blijft analyse mogelijk, maar wordt de niet-controleerbare versheid
  expliciet als databeperking gemeld. Dit is geen beurskalender of intradayfeed.
- Ontbrekende RoE, EPS, forward P/E, marktkapitalisatie of coverage blijven onbekend;
  geen fictieve nulwaarden/-1 in selectieparameters. Alle bekende cijfers blijven
  ongerond tot presentatie. Analistencoverage vereist een geheel niet-negatief
  aantal; expliciete nul is geldig, negatieve of fractionele aantallen niet.
- Marktkapitalisatiedrempel 5 miljard is nu expliciet USD en wordt uniform
  toegepast met een per-run gecachte Yahoo FX-koers van maximaal zeven dagen oud.
  Ontbrekende FX geeft een onbekend criterium, geen punt. De oorspronkelijke
  market_cap blijft apart in zijn hoofdvaluta beschikbaar; market_cap_usd is
  toegevoegd. GBp/GBX, ZAc en ILA worden voor totale marktkapitalisatie als
  GBP, ZAR en ILS behandeld; getoonde aandelenkoers houdt zijn subeenheid.
- Terugverdientijd combineert koers en boekwaarde/EPS alleen bij overeenkomende
  expliciete koers-/verslagvaluta. Bij subeenheden (pence/centen) wordt geen
  onbewezen eenheidsconversie van Yahoo per-aandeelvelden aangenomen: dat criterium
  blijft onbekend. Dit kan vooral de volledige 6/6-scan op zulke noteringen beperken.
  Andere criteria blijven beschikbaar. Relatieve EPS-groei combineert alleen de
  twee EPS-waarden en vereist geen vermenging met de aandelenkoers.
- De formule blijft (koers - boekwaarde) / trailing EPS bij positieve EPS, nu met
  minimum nul. Onder boekwaarde is er geen te verdienen boekwaardepremie; een
  negatieve termijn krijgt geen kunstmatig extra rankingvoordeel meer. Negatieve
  boekwaarde blijft een beschikbare accountingwaarde en verhoogt de termijn.
- Iedere unieke ticker, inclusief missers, wordt eenmaal geanalyseerd per run.
  Overlap wordt per beurs gerapporteerd/opgeslagen. Volgorde: score dalend,
  eindige terugverdientijd oplopend, ticker. Onbekende termijn komt bij gelijke
  score achter bekende termijnen. Kandidaten worden vóór de top-limiet geteld;
  hoogste scores onder de selectiedrempel worden als zodanig getoond.
- Databasebevestigingen worden geteld. Nul kandidaten geeft ook e-mail. Ontbrekende
  lijsten, onbruikbare tickers en ontbrekende criteria worden zichtbaar in een
  [ONVOLLEDIG]-rapport. Analysefouten, geheel onbruikbaar universum, mislukte
  opslag/e-mail en uitgeput rate-limitpauzebudget geven een foutstatus.
- Rate limiting herkent ook 429/hoofdlettervarianten. Bestaande pauzedrempels,
  pauzebudget en verhoogde throttle blijven. Na een stop worden niet-verwerkte
  tickers expliciet gemeld; eerder rate-gelimiteerde tickers worden niet opnieuw
  geanalyseerd tijdens dezelfde run. Gedeeltelijke scans zijn dus zichtbaar.
- Workflows tonen logs direct en voorkomen overlap van dezelfde workflow/branch.
  Dagelijkse timeout is 180 minuten, volledige scan behoudt 300 minuten.
  Planning blijft ongewijzigd. De onjuiste comment 'zaterdag 03:00 UTC' is
  gecorrigeerd naar de echte vrijdag 23:00 UTC (zaterdag 00:00/01:00 België).
  CLI accepteert uitsluitend live/full; de oude backtestmodus rekende niets uit.

## Grenzen en behouden regels

Dit is een eigen GARP-heuristiek met zes even zwaar tellende criteria, geen
gevalideerde strategie of exacte reproductie van een externe screener.
Live: RoE >=15%, terugverdientijd <=15 jaar, forward P/E positief en <=15,
verwachte EPS-groei >0%, marktkap positief en <=USD 5 miljard, coverage <=5.
Minimumscore 4, top 5. Full: RoE >=18%, termijn <=10 jaar, forward P/E <=12,
minimumscore 6, top 3; andere drempels gelijk. Een live score 4/6 kan dus een
onbekend of niet-gehaald criterium bevatten; de bot maakt niet alle criteria
stilzwijgend verplicht.

RoE en trailing EPS zijn actuele providerfundamentals, forward EPS/P/E zijn
onzekere ramingen; geen afstemming op een gemeenschappelijk boekjaar of garantie
op gelijke actualiteit. De terugverdientijd is een grove constante-winstformule,
geen discounted-cashflow-waardering. Het rapport noemt dit. Per-aandeelvelden
kunnen ADR- of providerbeperkingen hebben; Yahoo-data wordt niet onafhankelijk
gevalideerd. Forward P/E blijft de aangeleverde ratio en wordt niet stilzwijgend
uit andere mogelijk afwijkende providerperioden herberekend.

Dividendrendement is geen scorecriterium. De ongebruikte providerwaarde wordt
nu uitsluitend als dividend_yield_provider_raw in JSON bewaard, met onbekend bij
ontbreken; geen onzekere procent-/fractie-eenheid als berekend rendement voorgesteld.
Database-datum blijft scandatum; quote_date vermeldt afzonderlijk de beschikbare
koersdatum. Koersen tonen oorspronkelijke koerseenheden. Geen orders, portefeuille
of backtest uitgevoerd; geen winst- of rendementsclaims.

## Validatie en vervolg

31 nieuwe offline tests slagen: bekende 6/6-score, strengere full-drempels,
precisie, koersfallback/versheid, ontbrekende/oneindige fundamentals, coverage,
EPS, boekwaardepremie, valuta/subeenheden, USD-grens en FX-cache/versheid,
rate limiting en stop, deduplicatie, ranking, rapportage en database/e-mail.
Beide workflows zijn geparseerd en serviceconfiguratie/modes gecontroleerd.
Compile en git diff --check slagen. Yahoo, verzending en database zijn gemockt.
Geen echte notificaties of database-inserts bij deze Hoogland-tests.

```
python -m unittest discover -s tests -p 'test_01hoogl.py' -q
python -m py_compile bot_01hoogl.py
```

Live hertests staan open via run_01hoogl.yml (live/x-lijsten) en
run_01hoogl_full.yml (full/a-lijsten) op main. Controleer dekking, valuta/FX,
onbekende criteria, opslag en ontvangen e-mail. Geen nieuwe live-run gestart.

Andere open validatie: BeursSignalen moet na herstelcommit 25971a1 opnieuw worden
gestart voor x- en a-lijsten; de eerder aangeleverde x-run was technisch geslaagd
met onvolledige Europese koersdata. Cointegratierun technisch live geslaagd met
994/1000 tickers en e-mail; geen actieve selecties en daardoor geen echte
database-insert getest. Zie controle_01cointegr.md voor databeperkingen. MR EOD/ORB blijven open, ORB ook tijdens beursuren.
Volgende bot in bestandsvolgorde: bot_01kasstr.py.
