# Vaste prospectieve paperproef: paper_10d_usd_v1

Deze proef is een virtuele portefeuillevergelijking, geen handelsbot en geen
bewijs van rendement. Er worden geen orders, Telegramberichten of e-mails
verstuurd. Alleen refactor en zijn bestaande Supabase-database worden gebruikt.

## Vooraf vastgelegde regels

- Begin: paper_trials.started_at. Alleen daarna gearchiveerde prospectieve
  10d-voorspellingen tellen mee. Historische backtests worden niet omgelabeld.
- Eerste model: A Parameter-model v1. Broncodehash, invoerdefinities en vaste
  instellingen worden gecontroleerd. Rollende training en gewichten mogen zich
  volgens dezelfde vastgelegde regels aanpassen; de daadwerkelijk gebruikte
  gewichten, scores, modelinstellingen en trainingsgrenzen zitten in de snapshot.
- De eerste proef gebruikt geverifieerde USD-noteringen zonder internationale
  suffix, geselecteerd voordat toekomstige koersen worden opgehaald. Dat is een
  beperkte aandelenpopulatie; resultaten gelden niet automatisch voor Europa.
- Voor elke modelportefeuille $100.000 virtueel startkapitaal, geen leverage of
  extra stortingen. Maximaal tien modelposities, maximaal 10% van het beschikbare
  kapitaal per positie BIJ AANKOOP. Latere koerswegingen mogen veranderen.
- De gelijkgewogen baseline koopt alle kandidaten uit exact hetzelfde toegelaten
  universum. SPY is een tweede portefeuille met dezelfde instap, uitstap en kosten.
  Deze SPY-vergelijking is geen doorlopende buy-and-hold vanaf de eerste dag.
- Snapshot eerst definitief opslaan; aankoop tegen de opening op de eerste XNYS-
  sessie STRICT na de UTC-datum van de snapshot. Dus ook een snapshot vóór de
  Amerikaanse opening wacht conservatief tot een volgende UTC-dag/sessie.
- Elke mand wordt op de slotkoers van sessie 10 geliquideerd (instapsessie telt
  als sessie 1). Geen nieuwe mand vóór afsluiting. Bij een vertraagde volgende
  beslissing blijft het kapitaal cash; er worden geen gemiste manden teruggevuld.
- De volgende mand gebruikt het werkelijk berekende eindsaldo van de vorige mand.
  Dagelijkse mandjes worden niet opgeteld alsof het onafhankelijke kapitalen zijn.
- Fractionele aandelen zijn toegestaan. Per aankoop EN verkoop: 5 basispunten
  commissie en 10 basispunten nadelige koersafwijking. Dit zijn scenarioaannames,
  geen gemeten uitvoeringskosten. Geen rente, belastingen of bronheffing.
- Dividenden zijn bruto en worden als kasproxy op ex-datum gerekend, alleen als
  de positie al vóór die dag bestond. Betalingsvertraging wordt niet gesimuleerd:
  dit is een beperking voor kasbeschikbaarheid en toekomstige herbelegging.

## Opslag en controle

| Tabel | Inhoud |
|---|---|
| paper_trials | Onveranderlijk protocol, broncodehashes en starttijd |
| paper_batches | Onveranderlijke beslissing, kandidaatkoersen niet als uitvoering gebruikt; daarnaast verwerkingsstatus/resultaat |
| paper_prices | Eerste waargenomen OHLC, valuta en corporate actions, met waarnemingstijd |
| paper_nav | Dagelijks vermogen, kas, belegde waarde en aantal posities per portefeuille |
| paper_reports | Historie van beoordelingen en meetdatum |

Protocol, beslissingssnapshots, koersregels, NAV en rapporthistorie zijn tegen
UPDATE/DELETE beschermd met triggers. Alleen status en afgeleide result in een
batch mogen worden bijgewerkt. RLS staat aan; geen publieke API-toegang.

De koersbron is Yahoo, zonder automatische aanpassing en zonder repair. De
geplande handelskalender wordt vóór de uitkomsten uit exchange_calendars/XNYS
vastgelegd. Ontbrekende SPY-dagen mogen de instap of horizon dus niet verschuiven.
Een ontbrekende koers blokkeert de HELE gematchte vergelijking, inclusief de
baseline. Er wordt geen gunstige subset van beschikbare aandelen geëvalueerd.
Een splitsing vereist controle: Yahoo kan historische OHLC rond splitsingen
herberekenen. De proef mixt die niet blind met eerder vastgelegde prijzen.
Eerder opgeslagen prijzen worden niet achteraf vervangen. Er is nog geen
onafhankelijke tweede koersbron; verkeerde bronkoersen kunnen resultaten vertekenen.

## Rapportage en interpretatie

Dagelijkse waardering gebruikt uitsluitend afgesloten UTC-dagen. Daardoor loopt
de waardering bewust één kalenderdag achter; valued_through toont de echte datum.
De workflow draait dagelijks om 01:30 UTC (02:30 Belgische wintertijd), na een
succesvol A Parameter-model en handmatig. Er wordt geen nieuw model getraind door
deze workflow. Bij ontbrekende nieuwe voorspellingen blijft de proef wachten.
De eerste actieve uitvoering mag dus nul manden en nul rendement bevatten.

paper_reports.report bevat netto scenario-rendement, maximale terugval op
slotwaarderingen, netto winratio van gesloten posities, omloopsom gedeeld door
startkapitaal, aantal posities en verschil met beide baselines. Tijdens een
blokkade kan de laatste beschikbare waardering ouder zijn; lees active_status
en valued_through samen. Intraday terugval, liquiditeitslimieten en werkelijke
fills worden niet gemeten.

Vóór twaalf volledig afgesloten, niet-overlappende manden staat de status op
insufficient_history. Daarna blijft de status descriptive_only. Twaalf manden
zijn geen statistische bewijslast; perioden kunnen onderling afhankelijk zijn.
profitability_proven blijft false. Er is geen automatische vrijgave voor echt geld,
geen gegarandeerd rendement en geen selectie van alleen de beste kostenvariant.

Nieuw model of aangepaste instellingen/code: aparte proefversie en nieuwe start.
LightGBM, CatBoost en de meta-classifier kunnen pas eerlijk deelnemen met echte
vooruit gearchiveerde voorspellingen, vastgelegde training en dezelfde vooraf
vastgelegde kandidaten. Hun huidige historische tests tellen hier niet mee.

Praktische controle in Supabase:

```sql
SELECT id, started_at, protocol FROM public.paper_trials;
SELECT id, created_at, status, result->>'entry' AS entry,
       result->>'exit' AS exit FROM public.paper_batches ORDER BY created_at;
SELECT checked_at, report FROM public.paper_reports
WHERE trial_id='paper_10d_usd_v1' ORDER BY checked_at DESC LIMIT 1;
```

Bij review: corrigeer niet stilzwijgend de snapshots of koershistorie. Onderzoek
koersgaten/corporate actions, bewaar de blokkade en start zo nodig een gedocumenteerde
nieuwe proefversie. Bestaande voorspellingen en sel_par blijven behouden.
