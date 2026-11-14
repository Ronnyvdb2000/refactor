# Controle Ultimate-mailbot — 13 november 2026

Repository: Ronnyvdb2000/refactor. Wijzigingen betreffen `bot_00ultmail.py`,
zijn workflow en de bijbehorende regressietests. Andere bots zijn niet gewijzigd.

## Dataverwerking en rapportage

De bot hergebruikt `prepare_history` en `cached_history` uit `bot_00mail.py`.
Importeren van die module start geen scan of notificatie. Dit bewaart dezelfde
opschoning en downloadcache als de gewone mailbot, zonder twee implementaties.

Alleen complete, eindige OHLCV-rijen worden gebruikt; er worden geen koers- of
volumewaarden vooruit ingevuld. Elke ticker vereist minimaal 252 geldige sessies
en een laatste koers van maximaal zeven kalenderdagen oud. Ontbrekende/lege
lijsten, onbruikbare tickers en analysefouten worden in het rapport vermeld.
Dubbele tickers tussen sectoren worden eenmaal gedownload; ook missers worden
binnen dezelfde run gecacht.

Een ticker telt pas mee als alle zes strategieën succesvol zijn berekend.
Bij een fout blijven geen gedeeltelijke P/L of signalen achter. De rapportage
bevat per sector de aantallen gevraagd, geanalyseerd, overgeslagen en mislukt.
Zonder geslaagde analyses staat er 'niet beschikbaar', geen fictieve nulwinst.

Bij ontbrekende data krijgt de e-mail `[ONVOLLEDIG]` in het onderwerp. Onverwachte
analyse-/sectorfouten, nul geslaagde analyses of een mislukte e-mail geven exitcode
1. Gedeeltelijke datadekking blijft bruikbaar, met zichtbare beperkingen.

## Backtestcorrecties

- Alle zes strategieën gebruiken de laatste 252 handelsdagen; oudere gegevens
  dienen als indicatoropwarming. De oude trendbacktests gebruikten een andere
  periode dan de MRA-backtests.
- Signalen op slotdag j worden uitgevoerd op de opening van handelsdag j+1.
  De bot gebruikt het slot dat het signaal bepaalt niet tevens als gegarandeerde
  uitvoeringsprijs. Er worden geen intraday-stopfills gesimuleerd.
- De stop wordt na controle van de vorige slotkoers bijgewerkt; geen
  achteraf aangenomen stopprijs op dezelfde dag.
- Kosten volgen het gedeelde kostenmodel op de werkelijke instap- en
  uitstapnotional. Open posities worden tegen de laatste slotkoers gewaardeerd
  zonder een niet-uitgevoerde verkoop of fictieve sluitingskosten.
- De minimumhoudtijd van MRA Traag wordt in handelsintervallen sinds de
  daadwerkelijke instap geteld. MRA Snel gebruikt de geconfigureerde MA-periode.
- De vaste optelling van 100.000 is verwijderd. De getoonde uitkomst is de som
  van onafhankelijke posities met 2.500 rekeneenheden inzet, geen beheerde
  portefeuille met cashlimiet of herbelegging.

Trendvoorwaarden (MA/EMA, ADX, volume, trendfilter) en MRA-voorwaarden
(Bollinger, IBS, winstdoel en minimumhoudtijd) zijn behouden. Door correcte
uitvoering, kosten en het gelijke tijdvenster zullen P/L-cijfers veranderen.

## Valuta en notificaties

Koerssignalen worden aangeduid als waarden in de noteringsvaluta, niet steeds
als eurokoersen. De backtest gebruikt relatieve prijsveranderingen en een vaste
inzet in rekeneenheden. Er is geen FX-model: de uitkomst is dus geen werkelijk
eurovermogen. Het rapport vermeldt dit expliciet.

Telegram blijft uit en wordt niet aangeroepen. De e-mail blijft actief; alleen
een daadwerkelijk geslaagde retourstatus wordt als verzonden gemeld.
`PYTHONUNBUFFERED=1` toont voortgangslogs tijdens de run.

## Verificatie

42 offline tests geslaagd: 15 nieuwe ULT-tests plus de bestaande 27 tests voor
Kritische Selectie en de gewone mailbot. De ULT-tests controleren onder meer
volgende-opening uitvoering, werkelijke uitstapkosten, open posities, minimale
houdtijd, het 252-sessievenster, volledige tickerresultaten en verzendstatus.

```bash
python -m unittest discover -s tests -p 'test_00*.py' -q
```

Yahoo, SMTP en andere externe diensten zijn tijdens deze tests gemockt. Er is
geen e-mail verzonden. Een nieuwe live-run via `run_00ultmail.yml` op main is
nodig om actuele datadekking en verzending met de echte diensten te bevestigen.
De eerder gedocumenteerde elf onzekere tickercodes zijn niet gewijzigd.
