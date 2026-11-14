# Weekly Report livecontrole — 14 november 2026

Bron: aangeleverd Actions-log, run 43634539375 op commit ba25a2e; start 19:53:40 UTC, einde 20:30:32 UTC. Duur circa 36m51s. De eerste bijlage bevat de a-rapportjob; een tweede bijlage bevat x-run 43637968492. Nog geen opvolg/backtestlog aangeleverd.

## Resultaat vóór herstel

Module-import, scan, commit/push (92cbd3a) en artifact-upload geslaagd. Geen traceback/procesfout. Geen Telegram- of e-mailwaarschuwing in het log; aflevering is hiermee niet onafhankelijk bevestigd. Node-waarschuwing betreft upload-artifact@v4, niet de scanuitval.

14048 unieke tickers aangevraagd, 141 initiële batches. Het log bevat 501 regels met YFRateLimitError, één lokale database-is-locked-melding en veel niet gevonden symbolen. De oude fallback deed daarnaast zeer veel individuele retries. Bruikbare weekrendementen: Frankfurt 165/1418 (11,6%), Warschau 26/361 (7,2%), Helsinki 67/188 (35,6%). Het rapport publiceerde toch ranglijsten en JSON. Dit is geen betrouwbare volledige scan.

Alle 1418 ruwe Frankfurt-symbolen hadden geen suffix. Daardoor kon bijvoorbeeld AMD als Amerikaanse notering worden gebruikt onder het label Frankfurt. Warschau bevat al .WA maar veel namen worden door de provider niet herkend; Helsinki bevat ook onbruikbare symbolen. Deze namen worden niet op basis van een gok aangepast.

## Herstel

- Weekly invoer voegt .DE toe bij kale 043-symbolen; bestaande suffixen blijven behouden. .I wordt alleen voor 041 naar .IR vertaald. CSE-label voor lijst 055 gecorrigeerd. Bronbestanden niet gewijzigd.
- Eigen weekly batchdownloader: vier workers, timeout 15 seconden, één batchretry voor ontbrekende tickers. Geen individuele fallback-loop. Normale batchpauze 0,5s. Bij rate-limits één herstelpauze van 15s, daarna stoppen als throttling aanhoudt. Compacte samenvatting in plaats van duizenden providerregels.
- Per niet-lege lijst minimaal 80% bruikbare weekrendementen; bij kleine lijsten mag maximaal één ticker ontbreken. Lege kwaliteitslijsten zijn geen fout. Onder die grens geen ranglijst voor de lijst, expliciete Telegram-waarschuwing en rode workflow. Geen nieuwe JSON-publicatie en geen opvolg-database-export. Bij geslaagde lijsten blijft de werkelijk behaalde dekking zichtbaar.
- Versie-3-JSON vereist voor de winnaarsanalyse; de oude versie-2-uitvoer wordt geweigerd. De automatische backtest volgt alleen een geslaagde rapportjob.
- Dekking per ticker/lijst naar CSV-artifact; bij voortijdige providerstop alleen een foutmelding/artifact, geen volledige dekkingsmeting.
- Opvolg scant het hele universum met korte historie; lange historie alleen voor de uiteindelijke selectie. Hiermee vervalt de onnodige lange download voor alle 14048 tickers.
- Telegram blijft aan. Geen echte notificaties of database-updates tijdens deze herstelcontrole uitgevoerd.

## Verificatie en beperking

611 tests geslaagd, 9 sockettests overgeslagen, 17 subtests geslaagd. Negen nieuwe tests controleren beurscontext, begrensde retries, rate-limitstop en loggerherstel, batchnormalisatie, blokkering van publicatie/opslag bij lage dekking, behoud van notificaties en lange historie alleen voor selecties. RUFF en git diff --check geslaagd.

Kleine livecontrole van AMD.DE, SAP.DE en SIE.DE geslaagd: drie koersreeksen, basisdatum 2026-11-06, einddatum 2026-11-13; geen providerfout. Volledige 14048-tickerscan is nog niet opnieuw uitgevoerd. Geen garantie dat de nieuwe volledigheidscontrole slaagt: met name de bronsymbolen in Warschau/Helsinki vragen verdere verificatie. Parallelle runners blijven samen Yahoo-quota gebruiken; test voorlopig één workflow tegelijk. De wijziging voorkomt dat deze beperkingen als een volledig geslaagd rapport worden gepubliceerd.

## Tweede log: Weekly Report X

Run 43637968492 liep van 20:31:54 tot 20:33:37 UTC (circa 1m43s), zonder rate-limitmeldingen of procesfout. JSON-commit e62f6aa en artifact geslaagd. Geen Telegram/e-mailwaarschuwingen. Warschau 29/29, Helsinki 12/12 en Frankfurt 36/37. Oslo 3/4; Stockholm, Zurich, CSE en Oostenrijk/Slovenië/Slowakije hebben lege x-lijsten (0/0), wat geen downloadfout is. Deze observatie is expliciet opgenomen in de volledigheidstests.
