# Gedeelde OOS-factoranalyse

`python -m analyses.factor_evaluation --latest-research` maakt JSON en Markdown
in `results/factor_evaluation`. De workflow bewaart beide als artifact. Zij leest
uitsluitend Supabase-archieven in een read-only repeatable-read transactie: geen
training, providerverzoeken, notificaties of wijzigingen aan productie/backtests.
Een specifieke bron kan met `--research-run RUN_KEY` worden vastgepind. Bij
`--latest-research` worden de nieuwste afgesloten/bevroren gematchte bronnen per
regio/horizon aan het begin gekozen; hun exacte sleutels staan in het rapport.

Het rapport volgt de kernvragen van Alphalens zonder diens automatische
kalender-/labelherberekening over onze bevroren sessies heen te leggen. De
analyse verandert geen modelkeuze en promoveert geen kandidaat automatisch.

Per model, bron, horizon en prijsbasis:

- Spearman rank-IC per cohort en de gelijkgewogen gemiddelde IC.
- Vijf scorekwantielen, hun gelijkgewogen rendementen per datum, Q5 minus Q1,
  monotonie van de vijf gemiddelden en monotonie per cohort.
- Fractie nieuwe namen in Q5 tegenover de vorige scorecohort, inclusief de
  kalenderafstand tussen observaties. Dit is signaalturnover, geen werkelijk
  verhandeld vermogen. Scoreties worden niet alfabetisch in nepkwantielen verdeeld.
- Bruto en netto gevoeligheid van Q5 voor vaste round-trip kosten van 0/10/20/40 bp.
  De bevroren horizonportefeuilles sluiten hun hele mandje; naamoverlap levert dus
  geen veronderstelde kostenbesparing op. Deze vaste aftrek is een kostenscenario,
  geen exacte NAV-simulatie. De bestaande top-10 portefeuille-uitkomsten met
  commissie, slippage en waar relevant FX-kosten worden apart ongewijzigd getoond.

Kwantielen worden op alle scores bepaald voordat de uitkomsten worden bekeken.
Een onvolledige cohort levert geen IC/kwantielrendement op; ontbrekende verliezers
worden niet uit het universum verwijderd. Turnover blijft wel meetbaar zonder
rijpe rendementen. Ontbrekende statistieken zijn null, geen nulrendementen. Nieuwe parameter-, classifier- en kandidaatarchieven slaan
ook de werkelijke laatste trainingslabeldatum op; ontbrekende oude metadata
wordt niet achteraf verzonnen.

Historische gematchte proeven blijven gescheiden van prospectieve archieven.
Historische prijzen zijn aangepaste next-open/tot-horizon-close proxies, met de
bestaande EUR/FX-conversie. Historisch aangevulde features en koersuitsluiting
blijven beperkingen. Geen nieuwe dividend- of corporate-actionclaims worden
geïntroduceerd. Archieven zonder werkelijke trainingslabelgrens krijgen geen
rendementsbewijs; meerdere herberekeningen van dezelfde model/datum/basis worden
niet als extra observaties geteld (eerste vastlegging blijft gekozen).

Het aantal niet-overlappende selectie-/uitkomstvensters wordt vermeld. Dat is
geen bewijs van onafhankelijkheid. Minder dan 12 vensters krijgt expliciet
`insufficient_disjoint_history`. Er zijn geen p-waarden, geannualiseerde Sharpe
of samengesteld rendement uit overlappende dagelijkse baskets. Voor een echte
modelvergelijking moeten bron, universum, horizon en uitvoeringsbasis gelijk zijn.

Extra opbrengst van dit werk is diagnostisch: laat de hele rangschikking een
patroon zien, blijft Q5 na kosten positief, en hoeveel namen wisselen? Er wordt
geen extra financieel rendement door deze rapportage zelf gegenereerd.
