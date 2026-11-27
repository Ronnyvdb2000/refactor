# Historische reconstructie A Parameter-model (10d)

Dit is een aparte verkennende backtest. De lopende paperproef en haar vaste
selectie worden niet aangepast. Resultaten staan uitsluitend in
public.research_backtests en research_backtest_cache, niet in ml_runs of de paper-tabellen.

Het model berekent op iedere kandidaat-instapdatum zijn gewichten opnieuw met
de bestaande instellingen: minimaal 10 trainingsdatums, minimaal 20 aandelen
per trainingsdatum, 28 dagen rollend venster met bestaande fallbacks, minimaal
25 kalenderdagen embargo, |IC|>=0.02 en winsorisatie van IC op 0.15.
Een trainingsuitkomst wordt bovendien pas zichtbaar als de werkelijke datum van
haar tiende handelsdag vóór de aangenomen beslissing ligt. Ongeverifieerde of
ter controle gemarkeerde labels worden niet voor training gebruikt. Scoring
ziet geen toekomstige uitkomsten.

Na een volledige selectie worden US-noteringen gekozen op basis van de beurs
zoals die in de historische sel_par-rij staat, niet op basis van de huidige
lijst van tien aandelen. Daarna wordt een top 10 en een gelijkgewogen baseline
uit hetzelfde historische universum vastgelegd. SPY is de marktvergelijking.
Alle historische bronrijen worden vóór koersophalen bewaard. Trainingslabels
worden apart uit de koershistorie gereconstrueerd: vanaf de laatste koersdag op
of vóór de selectiedag tot de werkelijk tiende volgende handelsdag. Vervolgens
worden de selectie en gewichten vastgelegd voordat de uitvoeringsevaluator
toekomstige koersen gebruikt. De training maskeert iedere nog niet afgelopen
uitkomst. Dit is een causale codecontrole, geen destijds gepubliceerd archief.

Aangenomen beslismoment: de kalenderdag na de selectiedag om 06:00 UTC. Instap:
eerste XNYS-sessie strikt na die UTC-datum. Uitstap: slot van sessie 10, inclusief
instapsessie. De eerstvolgende selectie ligt na die uitstap. Geen overlappende
kapitaalpotjes. Begin met $100.000 per portefeuille; herbeleg het vorige
berekende eindsaldo. Per zijde 5 bp commissie en 10 bp nadelige koersafwijking.

Voor deze reconstructie worden Yahoo OHLC met auto_adjust=True gebruikt. Dit is
een historische total-returnproxy waarin dividenden en splitsingen verwerkt
zijn, geen letterlijk destijds uitvoerbare prijs. Daarom worden dividenden en
splitsingen niet nog eens toegevoegd. Dit verschilt van de live paperproef met
rauwe OHLC en een dividendkasproxy. Een ontbrekende prijs of niet-USD-valuta
blokkeert de hele gematchte periode; geen aandelen achteraf weglaten. Er wordt
niet doorgerekend over zo'n onopgeloste periode. Een laatste nog lopende periode
blijft open en wordt niet als afgesloten resultaat geteld.

Beperkingen: de historische kenmerken/selecties kunnen achteraf zijn
geconstrueerd; een onveranderlijk archief van wat destijds daadwerkelijk bekend
was ontbreekt. Actuele bronherzieningen en ontbrekende/delisted koersen kunnen
vertekenen. De instellingen waren al bekend vóór deze backtest en zijn geen
onafhankelijk gekozen experiment. Er is slechts enkele maanden historie, waarvan
een deel nodig is voor training. Daarom blijft profitability_proven=false.

De workflow is handmatig uitvoerbaar en draait één keer na wijziging van zijn
code. Geen terugkerende optimalisatie en geen Telegramberichten. Voortgang van
koersophalen wordt per ticker in research_backtest_cache bewaard; de hele
prijsverzameling wordt niet bij ieder aandeel opnieuw weggeschreven. Een herhaling van dezelfde Actions-run
behoudt zijn oorspronkelijke peildatum, beslissingen en instellingen. Een
gewijzigde definitie vereist een nieuwe run. Er is geen runtime-DDL.

```sql
SELECT run_key,created_at,status,
       result->'books' AS portfolios,
       result->'periods' AS periods,
       result->'quote_errors' AS missing_quotes
FROM public.research_backtests ORDER BY created_at DESC LIMIT 1;
```
