# Controle Kasstroom Onderwaardering — 14 november 2026

Repository uitsluitend Ronnyvdb2000/refactor; refactor-lab apart.
Script bot_01kasstr.py, workflow run_01kasstr.yml. De eerdere controleverslagen
van Hoogland en O'Shaughnessy zijn geraadpleegd voor valuta, verslagperioden,
onbekende data en rapportage. Geen gedeelde helper of andere bot aangepast.

## Boekjaren en FCF

Cashflowkolommen worden als expliciete jaarlijkse datums verwerkt, chronologisch
geordend en ontdubbeld. Lokale boekjaardatums blijven behouden bij tijdzones.
Ongeldige datumheaders tellen niet als boekjaar. De recentste jaarlijkse kolom
moet een eindige FCF bevatten en tussen vandaag en 550 dagen geleden liggen.
Een ontbrekende laatste FCF wordt nooit door een oudere jaarkolom vervangen.

Free Cash Flow wordt per boekjaar gelezen; bij ontbrekende waarde geldt voor
hetzelfde jaar de bestaande fallback Operating Cash Flow minus absolute capex.
Ook een deels beschikbare FCF-rij kan zo worden aangevuld. Oneindige waarden
worden ongeldig. Er is geen imputatie van ontbrekende OCF/capex als nul.

Minstens twee opeenvolgende beschikbare jaarwaarden zijn vereist. De afstand
van aangrenzende boekjaardatums moet 300–430 kalenderdagen bedragen. Oudere
waarnemingen na een gat/ontbrekende FCF worden niet aan de reeks vastgeplakt.
De onderbreking wordt gemeld; de werkelijk gebruikte eerste/laatste boekjaardatum
en het aantal jaren worden bewaard. Groei blijft de vergelijking van laatste
met eerste waarde van die reeks, niet noodzakelijk groei in ieder tussenjaar.
Consistentie betekent positief in alle gebruikte opeenvolgende jaren.

Payout gebruikt dividenduitgaven plus buybacks uit exact hetzelfde nieuwste
FCF-boekjaar. Beide posten moeten beschikbaar zijn; ontbrekend is geen nul.
Expliciete nul blijft geldig en krijgt, volgens het bestaande positieve-
payoutcriterium, geen punt. De provider kan kasuitgaven negatief of als absolute
bedragen leveren; beide worden als absolute uitgaven verwerkt. Er is geen aftrek
van aandelenuitgifte: dit blijft de oorspronkelijke bruto payout, geen netto
shareholder yield. Bij niet-positieve FCF is de payoutscore niet toepasbaar,
zonder dit op zichzelf als ontbrekende data voor te stellen.

## Piotroski: informatief, met boekjaarafstemming

Balans, resultaten en cashflow gebruiken hetzelfde nieuwste FCF-boekjaar en een
voorgaand gemeenschappelijk jaar op 300–430 dagen afstand. De eerdere selectie
van simpelweg kolom 0/1 kon verschillende boekjaren vergelijken.

De bestaande negen checks blijven als eigen eindbalans-benadering behouden:
positieve ROA, positieve OCF, stijgende ROA, OCF boven nettoresultaat, lagere
langetermijnschuld/activa, stijgende current ratio, geen verwatering, stijgende
brutomarge en hogere omzet/activa. ROA en activaomloop gebruiken de eindbalans,
geen historische begin-/gemiddelde activa: dit is geen geclaimde exacte
reproductie van de oorspronkelijke academische F-Score. Ook veranderingen in
shares door splits worden niet onafhankelijk gecorrigeerd.

Ontbrekende/onbruikbare inputs geven per criterium null (onbekend), niet false.
Een volledige /9-score verschijnt uitsluitend als alle negen berekenbaar zijn.
Anders toont het rapport aantallen positieve/beschikbare criteria en blijven
piotroski_score en de databasekolom null. De afzonderlijke flags staan als dict
in parameters.piotroski_detail. Piotroski blijft informatief en een secundaire
rankingfactor; het is geen negende selectiepunt. Een onbekende score komt bij
hetzelfde hoofdscoreaantal achter een bekende Piotroski-score.

## Overige numerieke en valutafouten

- Koers en marktkapitalisatie moeten positief/eindig zijn. Ongeldige currentPrice
  kan terugvallen op een geldige regularMarketPrice. De bestaande absolute
  FCF-yieldgrens van 100% blijft als plausibiliteitsfilter behouden.
- Ontbrekende totalDebt en totalCash worden geen nul meer. Beide moeten eindig
  en niet-negatief zijn; EBITDA moet positief zijn voor een leveragepunt.
  Werkelijk niet-positieve EBITDA geeft n.v.t., geen fictief schuldenvrij bedrijf.
- Analistencoverage vereist een geheel niet-negatief aantal. Expliciete nul
  blijft geldig; negatieve/fractionele/oneindige aantallen krijgen geen punt.
- Koers- en verslagvaluta moeten expliciet vergelijkbaar zijn. Bij verschillen
  worden FCF en market cap niet stilzwijgend gecombineerd. Voor totale bedragen
  worden GBp/GBX, ZAc en ILA als GBP, ZAR en ILS behandeld. De getoonde koers houdt
  zijn oorspronkelijke koerseenheid, bijvoorbeeld pence.
- De grens van 5 miljard is nu expliciet USD. Een per-run gecachte Yahoo FX-koers
  van maximaal zeven kalenderdagen oud zet market cap om naar market_cap_usd.
  De oorspronkelijke market_cap blijft beschikbaar. Ontbrekende/ongeldige FX
  geeft geen marktkappunt en staat in de databeperkingen. Een opgegooide
  provideruitzondering tijdens FX-ophalen wordt als analysefout gemeld.
- Indien Yahoo een koersdatum levert, moet die niet toekomstig en maximaal zeven
  dagen oud zijn. Ontbrekende koersdatum wordt expliciet als niet-controleerbare
  versheid gemeld. Dit is geen beurskalender of actuele tickfeed.
- Cijfers blijven ongerond tot presentatie. Ranking: hoofdscore dalend,
  bekende Piotroski-score dalend, FCF-yield dalend, ticker oplopend. Ties zijn
  onafhankelijk van de volgorde in de tickerlijsten.

## Behouden selectie en modelgrenzen

De acht bestaande punten blijven: FCF-yield >=5%, FCF laatste > eerste,
FCF steeds positief, provideromzetgroei >0%, netto schuld/EBITDA <=3,
positieve payout <=100% FCF, marktkap <=USD 5 miljard en coverage <=5.
Minimumscore 5/8 en maximaal vijf selecties per beurs blijven behouden.
Niet alle acht voorwaarden zijn verplicht; ook positieve FCF is geen afzonderlijk
hard filter. Een 5/8-kandidaat kan dus een of meer niet-gehaalde/onbekende criteria
hebben. Dat is de oorspronkelijke scoreopzet, geen garantie van onderwaardering.

Jaarlijkse FCF wordt gedeeld door actuele marktkapitalisatie, niet door een
historische of gegarandeerde TTM-waarde. Provideromzetgroei, schuld/cash en EBITDA
kunnen andere verslagperioden gebruiken; die bestaande snapshot-mix blijft
expliciet vermeld. Valutametadata en broncijfers zijn niet onafhankelijk geverifieerd.
DividendYield is geen scorecriterium en wordt alleen als onbewerkte
providerwaarde dividend_yield_provider_raw in JSON bewaard; geen onzekere
procent-/fractie-eenheid als berekend dividendrendement gepresenteerd.
Geen orders, portefeuilleboekingen, backtest of rendementsclaim.

## Rapportage en workflow

Telegram wordt niet geïmporteerd of aangeroepen. E-mail, bestaande database-
strategie bot_01kasstr en de bestaande secrets blijven. De database-datum is
scandatum; quote_date en fiscal_date staan afzonderlijk in parameters.

Iedere unieke ticker, inclusief missers, wordt eenmaal geanalyseerd. Overlap
blijft per beurs gerapporteerd/opgeslagen. Het rapport onderscheidt gevraagde,
geanalyseerde tickers, alle kandidaten en maximaal vijf topselecties. Een
algemene top-3 onder de selectiedrempel blijft zichtbaar als zodanig gemarkeerd.
Ook nul kandidaten geeft e-mail; lege lijsten, data-uitval en onbekende criteria
staan in [ONVOLLEDIG]. Een geldige gedeeltelijke scan kan met deze markering
slagen. Volledig onbruikbare data, onverwachte analysefouten en database-/e-mail-
fouten laten de run mislukken. Werkelijke databasebevestigingen worden geteld.
Zonder selecties valideert 0/0 geen live database-insert.

De workflow behoudt zijn planning (werkdagen 19:50 UTC), Python 3.11 en
contents: read. PYTHONUNBUFFERED toont logs direct; concurrency voorkomt overlap
van dezelfde workflow/branch. Timeout 180 minuten. Er zijn geen state-pushes.
CLI accepteert alleen live. De oude backtestmodus rekende niets uit en onbekende
modi starten niet meer stilzwijgend een live-run.

## Validatie en vervolg

45 offline regressietests geslaagd: bekende kasratio's/8-puntenscore en
informatieve 9/9-benadering, datums/volgorde/tijdzones, FCF-fallback per jaar,
ontbrekende huidige/middenjaren en gaten, verslagversheid, payout en expliciete
nul, schuld/cash/EBITDA, coverage, valuta/subeenheden, USD-grens en FX-cache,
koersvalidatie, Piotroski-afstemming/onbekende criteria, rankingprecisie,
deduplicatie, gedeeltelijke/lege scans, database/e-mailfouten en workflow.

```
python -m unittest discover -s tests -p 'test_01kasstr.py' -q
python -m py_compile bot_01kasstr.py
```

CLI-help en git diff --check slagen. Yahoo, e-mail en database zijn gemockt;
geen echte notificaties of inserts tijdens deze tests. Geen gedeelde helpers
gewijzigd. Live hertest staat open via run_01kasstr.yml op main: controleer
looptijd, verslagjaren, valuta/FX, ontbrekende payout/Piotroski-inputs, opgeslagen
aantallen en ontvangen e-mail.

Overige hertests blijven zoals eerder vastgelegd: BeursSignalen x/a opnieuw na
25971a1; Hoogland live/full na e1aa334; cointegratie technisch live geslaagd met
databeperkingen en nog geen echte insert getest; MR EOD en ORB open, ORB ook
tijdens beursuren. Volgende bot in bestandsvolgorde: bot_01marktsent.py.
