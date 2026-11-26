# Permanente modelbeoordeling in Supabase

De database is het hoofdarchief; bestaande CSV/JSON-artifacts zijn aanvullende
rapporten. Bestaande modelbestanden en Telegramberichten blijven behouden.

| Tabel | Inhoud |
| --- | --- |
| `ml_runs` | Model, versie, horizon, bronrun/poging/commit, opslagmoment, trainingsgrenzen, doel, modelhash en instellingen |
| `ml_params` | Elke berekende parameter: geselecteerd of afgewezen, IC, ruwe IC, IR, aantallen en getekend genormaliseerd gewicht; voor bomen feature importance |
| `ml_scores` | Alle gescoorde kandidaten per aandeel/datum, score, rang, top-N en bevroren selectiekoers |
| `ml_outcomes` | Status en werkelijke in-/uitstapkoersen en rendement per meetbasis |
| `ml_eval` | Volledigheid, top-N bruto, netto kostenscenario, aandeel stijgers, cohortgemiddelde, meeropbrengst en Spearman rangcorrelatie |
| `ml_compare` | Modelparen op dezelfde datum/horizon/modus en exact dezelfde gemeenschappelijke aandelen en koerssessies |

Alle tabellen zijn afgeschermd van publieke API-rollen. Dagelijkse workflows
schrijven met de bestaande databaseverbinding. Installatiescript:
`maintenance/sql/model_evaluation.sql`; de workflow doet geen runtime-DDL.
Scores en parameters worden als één transactie toegevoegd. Een herhaalde bronrun
met dezelfde sleutel kan geen andere inhoud over de oorspronkelijke voorspelling
schrijven. Nieuwe pogingen blijven apart traceerbaar; vergelijkingen gebruiken de
eerste gearchiveerde publicatie per modelversie, doel en selectiedatum.

## Welke modellen schrijven?

- `a_par_model`: alle scores van de beoordeelde dag, alle berekende IC-parameters
  inclusief afgewezen parameters, geselecteerde getekende gewichten en venster.
  `--geen-db-write` blijft volledig zonder archiefwrites.
- XGBoost V3 dagelijkse scorer: alle nieuw gescoorde kandidaten plus modelhash en
  feature importance, in dezelfde transactie als zijn bestaande scorelogboek.
  De oude scorer sluit reeds gescoorde aandelen uit: dit is geen identiek universum
  aan a_par. Vergelijkingen gebruiken uitsluitend de gemeenschappelijke set.
- LightGBM V2 en de gecontroleerde variant: historische testvoorspellingen en
  importance; trainingsgrens blijft vastgelegd.
- CatBoost V1: historische testvoorspellingen en importance, ook als de kandidaat
  op HOLD blijft. Opslag is geen promotie naar productie.

Een feature importance is geen getekend regressiegewicht en geen bewijs van
causaliteit. Tree-modellen worden niet als lineaire parameterverhouding beschreven.
Offline/CSV-proeven zonder `SUPABASE_DB_URL` kunnen blijven werken.

## Twee meetbasissen

**selection_close** gebruikt de gedateerde vervolgresultaten uit `sel_par` en
controleert de bevroren selectiekoers. Oude gekopieerde labels zonder echte
vervolgdatum/splitbasis en expliciete splitreviews blijven onvolledig. Dit is
signaalonderzoek vanaf een historische slotkoers, geen uitvoerbare aankoop na
publicatie. Reeds bestaande labels worden niet vervangen om resultaten te verbeteren.

**next_open** is uitsluitend voor nieuw gearchiveerde toekomstige voorspellingen:
Yahoo aangepaste opening van de eerste waargenomen sessie strikt na de UTC-
publicatiedag, tot de aangepaste slotkoers van sessie H (inclusief instapsessie).
De huidige dag wordt uitgesloten. Dit hergebruikt dezelfde definitie als de
bestaande XGBoost-beoordeling. Gaten, providerfouten en nog niet verstreken
termijnen blijven als status bewaard. Voltooide next_open-uitkomsten blijven
bevroren; bij een latere providerrevisie is een afzonderlijke controle nodig.

Bij geen enkele basis worden ontbrekende uitkomsten nul of winnaars. Zolang
ook maar één kandidaat ontbreekt, blijven de rendementscijfers van het volledige
cohort leeg. Bij vergelijkingen geldt dat voor alle gemeenschappelijke kandidaten.
De top 10 wordt gerangschikt vóór de uitkomsten worden bekeken; gelijke scores
worden op ticker gesorteerd. Per paar geldt dezelfde top-N (de kleinste ingestelde
N). Minder gemeenschappelijke aandelen dan N geeft geen volledige vergelijking.

## Dagelijks gebruik en grenzen

`Modelbeoordeling - Supabase` volgt succesvolle modelruns en draait dagelijks
om 17:30 UTC (18:30 Belgische wintertijd). Na wijzigingen aan de archiefcode op
main wordt ook een actuele a_par-berekening zonder notificaties uitgevoerd om
de nieuwe opslag direct te controleren. Deze krijgt de echte nieuwe opslagtijd;
het is geen achteraf gereconstrueerde oorspronkelijke voorspelling. Handmatig: `python -m
analyses.evaluate_models --cost-bps 20`. Standaard is 20 basispunten voor een
volledige koop/verkoop samen een expliciet scenario, geen gemeten brokerkosten.
Aandelenrendementen zijn in de eigen noteringsvaluta; dit is geen EUR-portefeuille
met wisselkoersresultaten. Het cohortgemiddelde is de gelijk gewogen baseline,
geen beursindex. Er is nog geen indexbenchmark, doorlopende portefeuillecurve,
maximale drawdown, p-waarde of bewezen statistische superioriteit. Overlappende
5/10/20/30/60-sessiecohorten mogen niet worden opgeteld of gecompound.

Historische tests en toekomstige voorspellingen worden nooit samengevoegd.
Gemeenschappelijke beschikbare horizons zijn 10, 30 en 60 sessies. Er bestaat
pas een viermodellenvergelijking wanneer alle vier werkelijk overeenkomende
voorspellingen hebben opgeslagen. LightGBM/CatBoost zijn momenteel historische
kandidaten: zij worden met deze wijziging geen dagelijkse productiescorers.
Oude artifacts worden niet achteraf als toekomstige voorspellingen gelabeld.
Het archief begint bij de eerste uitvoering met deze wijziging.

Voor een echte vergelijkende walk-forward-proef moeten alle vier bovendien in
één vooraf vastgelegd protocol dezelfde trainings-/testdatums doorlopen. Deze
opslag bewaart de daarvoor benodigde brongegevens en verbergt verschillen niet.
