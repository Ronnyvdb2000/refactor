# Tickercontrole mailbot — 13 november 2026

Deze correcties gelden voor `Ronnyvdb2000/refactor`, tickerlijsten 01–09.
Dubbele vermeldingen binnen één lijst zijn samengevoegd. Sectorindeling is behouden.
De lijsten worden ook door andere scripts gebruikt; de downloadcodes zijn daarom
rechtstreeks in de bestanden gecorrigeerd, niet via onzichtbare runtime-aliassen.

## Aangepaste symbolen

| Oud | Nieuw | Bedrijf / bron |
| --- | --- | --- |
| AYASF | AYA | [Aya Gold & Silver, Nasdaq](https://finance.yahoo.com/quote/AYA/) |
| SESG | SESG.PA | [SES, Parijs](https://finance.yahoo.com/quote/SESG.PA/) |
| PXT | PXT.TO | [Parex Resources](https://ca.finance.yahoo.com/quote/PXT.TO/community/) |
| SDF | SDF.DE | [K+S](https://finance.yahoo.com/quote/SDF.DE/) |
| PROX | PROX.BR | [Proximus](https://finance.yahoo.com/quote/PROX.BR/) |
| FVL | FVL.TO | [Freegold](https://finance.yahoo.com/quote/FVL.TO/) |
| CADLR | CADLR.OL | [Cadeler, eigen persbericht met Oslo-notering](https://finance.yahoo.com/sectors/energy/articles/cadeler-announces-planned-investments-exceptional-123100552.html) |
| KOSKI | KOSKI.HE | [Koskisen: handelscode KOSKI, Nasdaq Helsinki](https://storage.mfn.se/5094d74a-ba85-4d3e-aacf-f63c00a2baa4/koskisen-corporation-january-september-2026-interim-report.pdf); `.HE` is de Yahoo-suffix voor Helsinki |
| PBR.A | PBR-A | [Petrobras preferente ADR](https://finance.yahoo.com/quote/PBR-A/) |
| IBAB | IBAB.BR | [IBA, eigen persbericht met noteringscode](https://finance.yahoo.com/healthcare/articles/national-proton-therapy-center-portugal-050000792.html) |
| VTY | VTY.L | [Vistry](https://sg.finance.yahoo.com/quote/VTY.L/) |
| KINE.BR | KIN.BR | [Kinepolis](https://finance.yahoo.com/quote/KIN.BR/) |
| DSM.AS | DSFIR.AS | [DSM-Firmenich](https://finance.yahoo.com/quote/DSFIR.AS/history/); al aanwezige DSFIR.AS is niet dubbel behouden |
| REL.AS | REN.AS | [RELX: Amsterdam-code REN](https://www.relx.com/investors/shareholder-information); al aanwezige REN.AS is niet dubbel behouden |
| STVG | STVG.L | [STV Group](https://finance.yahoo.com/quote/STVG.L/) |
| ISO.V | ISO.TO | [IsoEnergy](https://sg.finance.yahoo.com/quote/ISO.TO/) |
| AIR, uitsluitend Parijs-lijst 05 | AIR.PA | [Airbus](https://finance.yahoo.com/quote/AIR.PA/history/); vermijdt verwarring met een Amerikaans AIR-symbool |
| UMI, metalenlijst 07 | UMI.BR | [Umicore, Brussel](https://www.umicore.com/en/investor-relations/) |
| JOY | JOY.TO | [Journey Energy](https://finance.yahoo.com/quote/JOY.TO/) |
| IPO.V | IPO.TO | [InPlay Oil](https://ca.finance.yahoo.com/quote/IPO.TO/history/) |

Yahoo-exchanges en suffixen: [databroninformatie](https://help.yahoo.com/kb/SLN2352.html).
Een geverifieerde noteringscode garandeert niet dat Yahoo elke aanvraag succesvol
beantwoordt; datadekking blijft per run zichtbaar in het rapport.

## Niet zonder bewijs vervangen of verwijderd

De volgende in het aangeleverde log mislukte symbolen zijn behouden:
`AX-PI`, `DTRN.BR`, `GOW-UN.TO`, `KIN`, `MOLY.V`, `OD7D.DE`, `RUPRF`,
`TNP-PF`, `VNP.AS`, `DVS`, `ODV`.

Voor onduidelijke symbolen is de bedoelde onderneming of aandelenklasse nog niet
eenduidig vastgesteld. DVS is een bestaande code voor Dolly Varden; een Yahoo-fout
bewijst geen foutieve code of delisting. Ook voor ODV is op basis van het log geen
verantwoorde vervanging vastgesteld. RUPRF heeft een mogelijke overname/delisting:
[Rupert-profiel](https://ca.finance.yahoo.com/quote/RUP.TO/). De positie wordt niet
automatisch vervangen door aandelen van een koper.

Deze resterende gevallen zijn dus geen stilzwijgend opgelost probleem. Ontbrekende,
ongeldige, te korte of meer dan zeven kalenderdagen oude dagkoersdata verschijnt
voortaan expliciet in het e-mailrapport en telt niet mee als een geslaagde analyse.
De ouderdomsgrens betreft de laatste beschikbare dagkoers van deze dagelijkse scan.

## Gedrag en validatie

De mailbot verstuurt uitsluitend e-mail. Ontbrekende data geeft `[ONVOLLEDIG]` in
het onderwerp. Analyse- en sectorfouten worden met context gelogd en opgenomen in
het rapport. Een verzendfout, nul geslaagde analyses of analyse-/sectorfouten geeft
exitcode 1; gedeeltelijk ontbrekende tickers geven een herkenbaar onvolledig rapport.
Dubbele downloads over sectoren worden binnen een run vermeden, ook voor missers.

Telegram staat ook uit voor Kritische Selectie (`TELEGRAM_ENABLED=0` in de workflow;
standaard uit in de bot). De e-mail en CSV van die bot blijven actief.

Offline tests: `python -m unittest discover -s tests -p 'test_00*.py' -v`.
Er zijn geen echte e-mails, Telegram-berichten of databasebewerkingen uitgevoerd
tijdens de tests. Een nieuwe live-run is nodig voor controle bij Yahoo en SMTP.
