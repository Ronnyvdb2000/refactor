# Controle Seizoenseffecten — 14 november 2026

Scope: bot_01repititief.py en run_01repititief.yml in Ronnyvdb2000/refactor. De bestaande gecontroleerde tickerlijsten zijn niet gewijzigd. De workflow gebruikt x-lijsten; de oude naam FULL was misleidend.

## Signalen en data

Negatieve effecten worden pas na BH-correctie afgewezen, zodat negatieve hypothesen de testfamilie niet vooraf verkleinen. BH blijft per beurs/buckettype; geen claim van een globale FDR-garantie over de drie families of gecorreleerde dagrendementen. Ongeldige p-waarden tellen conservatief als 1; constante reeksen leveren geen significantie. Onbekende/oneindige rendementen worden niet getest.

Dagstatistieken zijn behouden: gemiddelde dagelijkse close-to-close rendementen op de betreffende weekdag of binnen de huidige maand/het kwartaal. Dit zijn geen volledige maand-/kwartaalrendementen, geen causale seizoensmeeropbrengst tegenover een benchmark en geen netto voorspelling. De score is een heuristische som van positief significante gemiddelden gewogen met 1-p. Maximaal acht selecties per beurs; gelijke scores op ticker gesorteerd.

Gedateerde, positieve eindkoers; minstens 1250 geldige rendementen en vijf jaar datumbereik; afgeronde sessies vóór vandaag; maximaal zeven kalenderdagen oude eindkoers. Lokale handelsdatums blijven behouden. Dubbele datums worden gesorteerd en ontdubbeld. Ongeldige oudere slotkoersen worden NaN; geen forward-fill of rendement over die gaten. Gaten langer dan zeven dagen worden evenmin als dagrendement gebruikt. De gedeelde downloader kan eerder ontbrekende Close-rijen verwijderen: kalender-/sessievolledigheid is daardoor niet onafhankelijk bewezen en repair is niet toegevoegd.

Weekend-doeldag wordt maandag; beursfeestdagen en openingstijden worden niet gemodelleerd. Rapport en database bevatten signal_date en target_date. Koersen behouden precisie; de weergave zegt bronkoerseenheden, geen verzonnen eurovaluta. Downloads in batches van maximaal honderd; cache per unieke ticker, ook bij overlap tussen beurzen.

## Historie en backtest

De live historische check is expliciet beschrijvend na selectie, niet walk-forward of onafhankelijke validatie. Zij handelt hetzelfde achteraf gekozen patroon door historische jaren vanaf 2021, met een synthetische indexbasis 100 en kosten. PF wordt uit afzonderlijke trades berekend; zonder verlies is hij null/n.v.t., geen kunstmatig extreem getal.

De afzonderlijke backtest herberekent bucketstats uitsluitend op eerdere kalenderjaren. BH gebeurt per beurs, jaar, buckettype en waarde. Hij test alle significant positieve waarden; dit is dus geen reconstructie van de acht hoogst gerangschikte live selecties per scanmoment. Huidige tickerlijsten en actuele historische aanpassingen bevatten mogelijk survivorship/revision bias.

Hypothetische kalendertransacties: vorige sessieslotkoers naar doelweekdagslotkoers, of slotkoers vóór maand/kwartaal naar slotkoers aan het einde. Maanden/kwartalen vereisen data rond beide grenzen en een waarneming na afloop; huidige onvoltooide perioden worden niet fictief gesloten. Dit is een kalenderconventie en geen uitvoeringssimulatie van een live rapport midden in de periode. Omdat het jaarmodel pas met afgeronde voorgaande kalenderjaren beschikbaar is, worden entries in het vorige jaar uitgesloten (dus januari/Q1-trades met december-entry en de eerste weekdagtrade kunnen wegvallen). Feestdagen zijn niet apart gevalideerd; grenzen gebruiken maximaal zeven dagen marge.

Backtestindex wordt per ticker vóór het testvenster op 100 gezet; geen FX-conversie of werkelijk aantal aandelen. Elke trade heeft een onafhankelijk notional van 2500 synthetische eenheden inclusief aankoopkosten; geen geforceerd minimumaandeel. Kosten max(15, 0,35% notional) per kant; 0,1% slippage per kant; de ongefundeerde 10%-belasting verwijderd. Geen gedeelde cash/portefeuille, compounding, totaalrendement of totale EUR-winst. Trade-CSV bevat beurs, entry-/exitdatums en eenheden, ook met nul trades. BACKTEST_START/END begrenzen het venster inclusief einddatum; voldoende eerdere trainingsdata wordt expliciet gedownload.

## Meldingen en verificatie

Telegram-aanroepen en workflowverwijzingen verwijderd. E-mail wordt ook bij nul kandidaten of ontbrekende data geprobeerd. Databasebevestiging per selectie, totalen en datadekking in het rapport. Verwachte databeperkingen geven [ONVOLLEDIG]; programmeer-, download-, opslag- of mailfouten maken de job rood. Geen bruikbare analyses is een fout. Workflow behoudt database en e-mail, Python 3.11, krijgt read-permissies, concurrency, 180 minuten timeout en ongebufferde logs.

Validatie: 29 offline tests onder lokale Python 3.12, met synthetische histories en gemockte Yahoo/database/e-mail. Onder meer BH, negatieve effecten, precisie, datums, weekend, gaten, training zonder testjaar, complete kalenderperioden, kosten/notional, ontbrekende PF, cache, lege selectie, opslag/mailfouten, lege en gevulde backtest-CSV en beursfamilies. Geen live notificaties, database-aanvragen of volledige historische universumbacktest uitgevoerd. Live hertest van run_01repititief.yml blijft open.
