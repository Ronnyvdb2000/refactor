# Tickerlijsten naar data — 20 november 2026

89 `tickers_*.txt`-bestanden zijn uit de hoofdmap naar `data/` verplaatst. Namen en inhoud zijn byte voor byte behouden, inclusief bron-, master-, actieve en delistedlijsten en de overige tickeruniversums. Geen nieuwe selectie of download uitgevoerd.

## Afnemers

- `common.io.bouw_bestandslijst` levert de nieuwe paden; de bestaande bots, combiners en fundamentals-builder die deze helper gebruiken volgen die paden automatisch.
- De lokale tickerlijstgenerator in de technische builder, de MRA-filter, Superbot, Volhunter, beide mailrapporten en de MR/ORB-lijsten zijn afzonderlijk aangepast.
- De gedeelde beurslabelbepaling en de US-beurscontrole gebruiken de bestandsnaam zonder mapvoorvoegsel. Bestaande beursidentiteit blijft behouden.
- Weekly leest de lijsten uit `ROOT/data/`, met behoud van kale namen als interne sleutels voor de bestaande alias-, beurs- en deelmarktregistraties.
- De filterworkflow bewaart a/m/x/d-bestanden onder `data/`. Broncorrecties en teruggezette delisted tickers in a-lijsten worden daardoor samen met de overige filterresultaten gepubliceerd.
- Scripts blijven vanuit de repository-root starten. Er is geen terugval naar verouderde rootlijsten toegevoegd.

De vaste universums zijn niet veranderd. De forward-returnbuilder haalt zijn werkvoorraad uit de database en heeft geen eigen tickerbestandspad. TradingAgents consumeert zijn bestaande pick-bestanden; die code en refactor-lab zijn niet gewijzigd. De dagelijkse onderzoekssimulatie blijft haar bestaande read-only databasepad op de aparte branch gebruiken.

## Controle

Volledige lokale suite: 886 tests geslaagd, 13 overgeslagen, 19 subtests geslaagd. De nieuwe regressies controleren bestaande tickerlijsten, beurslabels, lokaal lezen/schrijven van alle filtervarianten en de daadwerkelijke git-add-opdracht van de filterworkflow in een tijdelijke repository. De overgeslagen tests blijven zichtbaar; dit is geen claim van een volledig live geteste databaseketen.

Alle 89 verplaatste bestanden zijn tegen de originelen vergeleken. Audit van 67 workflows: geen fouten of waarschuwingen. Python-syntax en YAML/shell-syntax van de aangepaste workflows gecontroleerd. De bestaande CI-workflow heet nu **Repository layout and workflow checks** en voert ook tickerpad-, filter-, weekly-reader- en MR-tests uit zonder productie-scans, secrets of databasewrites.
