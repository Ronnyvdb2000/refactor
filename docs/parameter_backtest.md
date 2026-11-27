# Historische reconstructie A Parameter-model (5d, 10d, 20d, 30d, 60d)

Dit is een aparte verkennende backtest. De lopende paperproef en haar vaste
selectie worden niet aangepast. Resultaten staan uitsluitend in
public.research_backtests en research_backtest_cache, niet in ml_runs of de paper-tabellen.

Het model berekent op iedere kandidaat-instapdatum zijn gewichten opnieuw met
de bestaande instellingen: minimaal 10 trainingsdatums, minimaal 20 aandelen
per trainingsdatum, 28 dagen rollend venster met bestaande fallbacks, een horizonafhankelijk kalenderembargo van minimaal
25 dagen, |IC|>=0.02 en winsorisatie van IC op 0.15.
Een trainingsuitkomst wordt bovendien pas zichtbaar als de werkelijke datum van
haar horizon-einddag vóór de aangenomen beslissing ligt. Ongeverifieerde of
ter controle gemarkeerde labels worden niet voor training gebruikt. Scoring
ziet geen toekomstige uitkomsten.

Na een volledige selectie worden US-noteringen gekozen op basis van de beurs
zoals die in de historische sel_par-rij staat, niet op basis van de huidige
lijst van tien aandelen. Daarna wordt een top 10 en een gelijkgewogen baseline
uit hetzelfde historische universum vastgelegd. SPY is de marktvergelijking.
Alle historische bronrijen worden vóór koersophalen bewaard. Trainingslabels
worden apart uit de koershistorie gereconstrueerd: vanaf de laatste koersdag op
of vóór de selectiedag tot de werkelijk vijfde, tiende, twintigste, dertigste of zestigste volgende handelsdag, afhankelijk van de horizon. Vervolgens
worden de selectie en gewichten vastgelegd voordat de uitvoeringsevaluator
toekomstige koersen gebruikt. De training maskeert iedere nog niet afgelopen
uitkomst. Dit is een causale codecontrole, geen destijds gepubliceerd archief.

Aangenomen beslismoment: de kalenderdag na de selectiedag om 06:00 UTC. Instap:
eerste XNYS-sessie strikt na die UTC-datum. Uitstap: slot van sessie 5, 10, 20, 30 of 60, inclusief
instapsessie. De eerstvolgende selectie ligt na die uitstap. Geen overlappende
kapitaalpotjes. Begin met $100.000 per portefeuille; herbeleg het vorige
berekende eindsaldo. Per zijde 5 bp commissie en 10 bp nadelige koersafwijking.

Voor deze reconstructie worden Yahoo OHLC met auto_adjust=True gebruikt. Dit is
een historische total-returnproxy waarin dividenden en splitsingen verwerkt
zijn, geen letterlijk destijds uitvoerbare prijs. Daarom worden dividenden en
splitsingen niet nog eens toegevoegd. Dit verschilt van de live paperproef met
rauwe OHLC en een dividendkasproxy. Onvolledige historische koersen leveren een
cashplaats op: de overige aandelen krijgen geen groter budget en er worden geen
vervangers gekozen. Deze uitsluiting gebruikt achteraf bekende koersbeschikbaarheid
en kan de uitslag vertekenen. De uitgesloten aandelen en budgetten staan per
periode in het resultaat. Een laatste nog lopende periode
blijft open en wordt niet als afgesloten resultaat geteld.

Beperkingen: de historische kenmerken/selecties kunnen achteraf zijn
geconstrueerd; een onveranderlijk archief van wat destijds daadwerkelijk bekend
was ontbreekt. Actuele bronherzieningen en ontbrekende/delisted koersen kunnen
vertekenen. De instellingen waren al bekend vóór deze backtest en zijn geen
onafhankelijk gekozen experiment. Er is slechts enkele maanden historie, waarvan
een deel nodig is voor training. Daarom blijft profitability_proven=false.

De workflow is handmatig uitvoerbaar en draait één keer na wijziging van zijn
code. Geen terugkerende optimalisatie. Na de berekening volgt een Telegrammelding
met status, netto rendement inclusief cash, maximale terugval, laatste belegde
budgetten en overgeslagen aandelen. Ook een tussentijds resultaat wordt als
onvolledig gemeld als de berekeningsstap niet slaagt. De melding hoort bij precies
deze Actions-run en pakt nooit stilzwijgend een oudere uitslag. Voortgang van
koersophalen wordt per ticker in research_backtest_cache bewaard; de hele
prijsverzameling wordt niet bij ieder aandeel opnieuw weggeschreven. Een herhaling van dezelfde Actions-run
behoudt zijn oorspronkelijke peildatum, beslissingen en instellingen. Een
gewijzigde definitie vereist een nieuwe run.

De workflow biedt twee bronnen: `cached` (standaard) herberekent ongewijzigde,
eerder vastgelegde brondata en koersen in een afzonderlijke run per horizon met een verwijzing
naar het origineel. Voor dezelfde horizon blijven de bestaande selecties bevroren;
voor een andere horizon worden trainingslabels, gewichten en rangschikkingen
opnieuw walk-forward opgebouwd, met alleen op de beslisdatum bekende labels. Het model en de koersbasis moeten overeenkomen. Er worden
geen nieuwe koersen opgehaald. De oorspronkelijke selecties en resultaten worden niet overschreven. `fresh` maakt
een nieuwe bronselectie en haalt koersen op. Bij een push wordt `cached` gebruikt.

Alleen de notificatiestap ontvangt Telegramsecrets. Zij initialiseert idempotent
`research_backtest_notices`, met row-level security en ingetrokken publieke
rechten. De verzendclaim wordt vóór HTTP vastgelegd. Een bevestigde afwijzing mag
opnieuw geprobeerd worden; een onzekere bezorging vraagt controle om dubbelen te
voorkomen. De berekening zelf verandert geen schema en gebruikt geen Telegramsecrets.

```sql
SELECT run_key,created_at,status,
       result->'books' AS portfolios,
       result->'periods' AS periods,
       result->'quote_errors' AS missing_quotes
FROM public.research_backtests ORDER BY created_at DESC LIMIT 1;
```

## Beperkte diagnose bij ontbrekende kandidatenkoersen

Als de volledige kandidatenbaseline niet te reconstrueren is, kan de
ongewijzigde top 10 afzonderlijk met SPY worden vergeleken. Dat resultaat staat
onder result.diagnostic_model_vs_SPY. De oorspronkelijke status en uitkomst van
de volledige vergelijking blijven behouden. Er worden geen modelposities of
meetperioden verwijderd. Ontbrekende historische modelkoersen laten de
oorspronkelijke allocatie cash; ook deze uitsluiting staat in de rapportage. Dit is geen gerepareerde kandidatenbaseline
en geen bewijs dat het model beter kiest dan het hele selectie-universum.

```sql
SELECT result->'diagnostic_model_vs_SPY' AS limited_comparison
FROM public.research_backtests ORDER BY created_at DESC LIMIT 1;
```


## Vergelijking van horizons

De workflow voert 5, 10, 20, 30 en 60 handelsdagen afzonderlijk uit, met $100.000
per portefeuille en dezelfde kosten, cashplaatsen, SPY en gelijkgewogen baseline.
Iedere horizon heeft een eigen runsleutel (`a_par_5d:<Actions-ID>`, enzovoort) en
een eigen Telegrammelding. Een probleem bij één horizon annuleert de andere
horizons niet. De berekeningen lopen na elkaar om database- en koerslocks te
respecteren. De live paperproef en haar horizon veranderen niet.

Een langere horizon krijgt een passend embargo en pas trainingslabels nadat de
werkelijke horizon-einddag verstreken is. Ontbreekt daardoor voldoende training,
dan luidt de uitkomst `insufficient_history`; er wordt geen rendement van 0%
verzonnen. Een nog lopende beleggingsperiode blijft als zodanig herkenbaar.

De beschikbare trainings- en beleggingsperioden kunnen per horizon verschillen.
Telegram toont daarom ook de werkelijke beleggingsperiode. Vergelijk losse
rendementspercentages niet alsof alle horizons op dezelfde datum begonnen en
evenveel onafhankelijke perioden hadden. Er wordt geen beste horizon automatisch
gekozen en geen productie-instelling gewijzigd.
