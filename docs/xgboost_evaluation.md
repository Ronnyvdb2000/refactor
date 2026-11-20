# Evaluatie van nieuwe V3-voorspellingen

`python -m analyses.evaluate_xgboost --telegram` leest het V3-voorspellogboek en maakt `results/xgboost_evaluation.json` en `.md`. Geen CSV. De workflow `evaluate_xgboost.yml` loopt dagelijks om 06:30 UTC en kan handmatig gestart worden. De JSON bevat individuele uitkomsten, dekking, cohorten, modelvingerafdrukken en aannames. Artifacts worden 90 dagen bewaard. Telegram krijgt een beknopt rapport. De evaluatie gebruikt een alleen-lezen databasetransactie en voert geen orders uit.

## Voorspellingen vastleggen

Bij het opslaan van nieuwe V3-scores maakt de scorer zo nodig `public.xgboost_prediction_log`. Dit is de enige nieuwe tabel; de bestaande scoretabellen, modelnamen en historische rendementen worden niet aangepast. De databasegebruiker van de scorer moet de tabel mogen aanmaken en vullen. Bij een fout rollen zowel de nieuwe score-inserts als het logboek terug: er wordt geen geslaagde, controleerbare opslag geclaimd zonder logboek.

Alleen daadwerkelijk nieuw ingevoegde scores worden opgenomen. Een conflict in de bestaande scoretabel levert dus geen nieuwe voorspelling op. Het logboek bewaart ticker, oorspronkelijke selectiedatum, horizon, score, SHA256 van het gebruikte PKL-modelbestand, broncommit, run/poging en database-tijdstip. De primaire sleutel voorkomt overschrijven bij herhaling. De code biedt geen UPDATE-route, maar databasebeheerders kunnen de tabel uiteraard wijzigen. Dit is geen onveranderbaar extern auditarchief.

Het tijdstip is het vastleggen van de berekende score, niet een ontvangstbevestiging van Telegram. Een mislukte notificatie maakt een opgeslagen voorspelling niet ongedaan. Training wordt niet gestart door deze evaluatie. De bestaande combiners blijven `model_versie=xgboostV3` gebruiken; de vingerafdruk staat apart en verandert hun filters niet.

## Uitkomsten en vergelijking

- Instap: aangepaste openingskoers op de eerste beschikbare handelssessie strikt na de UTC-datum van vastlegging. Zo worden de eerdere selectiedag en bewegingen vóór berekening niet als resultaat van de voorspelling meegeteld. Deze conservatieve daggrens is bedoeld voor het bestaande Europa/Noord-Amerika-universum.
- Uitstap: aangepaste slotkoers van de 10e, 30e of 60e sessie, inclusief de instapsessie. De lopende UTC-dag wordt nooit meegenomen. Yahoo levert aangepaste Open/Close; dit is geen uitvoeringsbewijs of brokerafrekening.
- Eén cohort per scoringsrun, horizon en modelvingerafdruk. Meerdere selectiedatums van dezelfde ticker tellen eenmaal, met de nieuwste selectiedatum. Rangschikking vindt plaats vóór het bekijken van rendementen; gelijke scores worden op ticker gesorteerd.
- Top 10 wordt vergeleken met alle unieke **nieuw gescoorde kandidaten in datzelfde cohort**. Dit is geen vergelijking met een beursindex, alle oorspronkelijke selecties of de oude gezamenlijke Telegram-top over meerdere horizons. Minder dan tien kandidaten wordt expliciet gerapporteerd.
- Een cohort wordt alleen berekend wanneer alle kandidaten geldige, volgroeide uitkomsten hebben. Ontbrekende koersen, providerfouten en korte historie worden zichtbaar als onvolledig, nooit verwijderd om alleen overlevende aandelen te evalueren of als nulrendement geteld.
- Rapport per cohort: gemiddelde, mediaan, winpercentage en slechtste individueel rendement; daarnaast verschil met de cohortbaseline. Horizonsamenvatting weegt dagen gelijk; herhaalde runs binnen een dag worden eerst gemiddeld. Het aantal publicatiedagen is geen aantal onafhankelijke waarnemingen.
- Standaard kosten **0 bp**: netto is dan gelijk aan bruto, dus geen claim na echte transactiekosten. `--cost-bps 50` trekt als scenario 0,50 procentpunt retourkosten af per positie. Geen liquiditeitsmodel, FX-omrekening of werkelijke portefeuille-drawdown. Positiegrootte, overlap en kasbeheer worden niet gesimuleerd.

Lange koersgaten, dubbele/ongesorteerde datums en ongeldige Open/Close blokkeren uitkomsten. Korte ontbrekende sessies en delistings zijn zonder officiële beurskalender niet volledig te onderscheiden van normale sluitingsdagen. Resultaten blijven daarom beschrijvend, zonder p-waarde, onafhankelijkheidsclaim of uitspraak dat een model aantoonbaar beter is. De volledige JSON laat onvolledige cohorten zien.

## Bestaande scores

De reeds opgeslagen `xgboost_scores` worden geteld, maar niet achteraf van een modelhash voorzien. Hun selectiedatum is niet noodzakelijk de datum waarop de voorspelling gemaakt werd. Ze worden niet met `forward_returns` samengevoegd als bewijs van prospectieve prestaties. Dit voorkomt dat de koersbeweging van 18/19 november vóór een voorspelling op 20 november als toekomstig resultaat telt.

De bestaande dagelijkse forward-returnworkflow en trainingsvalidatie blijven afzonderlijke routes: nuttig voor historische strategie- en trainingsanalyse, geen vervanging van deze opvolging. Het nieuwe rapport kan aanvankelijk terecht melden dat het logboek nog leeg is of alle horizons nog moeten aflopen. De eerste nieuwe scorerun begint de registratie; geen oude runs worden opnieuw uitgevoerd.

## Controle

Offline tests controleren exacte instap/uitstap, uitsluiting van lopende dag en oudere selectiedag, onrijpe horizons, prijsfouten, ontbrekende kandidaten, unieke tickers, vaste kosten, fingerprints, herhaalde dagen en transactiegedrag bij scoreconflicten/logboekfouten. De GitHub-controleworkflow voert deze tests mee uit. Een geslaagde test is geen al uitgevoerde live migratie of bewezen rendement.
