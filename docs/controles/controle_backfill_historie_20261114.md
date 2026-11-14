# Controle technische backfill — 2026-11-14

## Oorzaak en herstel

Yahoo gaf voor ARX.TO en BEZ.L ieder slechts één koersbalk terug, ook bij een aanvraag van de volledige historie. EMA begon daardoor op de slotkoers en MACD op nul. Een volgende begrensde aanvraag leverde voor deze tickers geen data. Deze historie kan nu niet betrouwbaar worden aangevuld.

De backfill gebruikt nu opwarmvensters per feature, controleert echte OHLCV-balken en onderbrekingen, en vergelijkt aanwezige MA50/RSI vóór een update. Afwijkende of niet berekenbare controles blokkeren de rij. De bestaande indicatorformules en niet-NULL-velden blijven behouden. Vier bestaande ARX.TO-rijen met nul-MACD en gelijke EMA's zijn verdacht; deze zijn niet overschreven omdat er geen betrouwbare vervangende historie beschikbaar is.

## Databasecontrole en aanvulling

358 openstaande rijen bij 26 tickers onderzocht. Met de huidige brondata konden 25 rijen betrouwbaar worden aangevuld: 24 DDS en één SMG.SW. Alleen ontbrekende hv60, dagen_sinds_low52w en vol_ratio_50d zijn ingevuld (respectievelijk 24, 25 en 22 waarden). De SQL-update vereiste tevens dat de opgeslagen MA50/RSI nog overeenkwamen met de gecontroleerde momentopname. Na de update kwamen alle 25 rijen overeen met het berekende plan.

Overige ontbrekende waarden blijven staan: ontbrekende brondata, onvoldoende historie of ontbrekende/afwijkende controles. De backfill hoeft hiervoor niet herhaald te worden zolang geen betere historie beschikbaar is. Er zijn geen notificatiebots uitgevoerd.

## Verificatie

576 tests geslaagd, 9 tests overgeslagen wegens niet beschikbare lokale sockets; 17 subtests geslaagd. Acht nieuwe tests controleren korte historie, legitieme nulwaarden, causaliteit, ongeldige balken, koersgaten, dubbele datums en blokkering vóór een update. De bestaande tien workflowtests controleren modulepaden, dry-run/apply en foutcodes via tee. Algemene pytest-collectie vanuit de root stuit op het losse live verbindingsscript test_verbinding.py wegens ontbrekende dotenv; de volledige offline suite onder tests/ slaagt.

De workflow blijft .github/workflows/backfill.yml en voert python -m maintainance.backfill_technische_features uit. Directe loguitvoer is aangezet; dry-run blijft standaard actief. Download- en verwerkingsfouten resulteren na de samenvatting in een foutcode.
