# Generieke technicals — 19 november 2026

## Aangeleverd log

Run van 05:03:35 tot 05:09:29 UTC op `3edf0248494ab67c8de26164355fee59a1637ca7`.
Python en dependencies installeren succesvol; het script wordt uitgevoerd.
Resultaat: 446 van 1000 tickers geschreven, 554 overgeslagen, 44,6% dekking.
De vereiste 80% wordt niet gehaald en het proces eindigt terecht met exitcode 1.

De 446 geslaagde rijen zijn per ticker gecommit; de rode status draait deze
opslag niet terug. De samenvatting toont geen verwerkingsfouten. De oorspronkelijke
code registreerde bij de normale skip-paden geen reden, zodat dit log niet bewijst
welke redenen samen de 554 overgeslagen tickers verklaren.

`ING.AS` geeft een Yahoo-404/no-timezone-melding. Dat bewijst een onbruikbare
providerrespons in deze run, geen delisting of correcte vervangende handelscode.
De Node-deprecationmelding is niet de reden voor de Python-exitcode.

## Correctie

- Per ticker een CSV-regel met status, reden, detail, aantal historiebalken en
  gekozen koersdatum: `results/generieke_technicals_dekking.csv`.
- Redenen voor lege historie, lege indicatorhistorie, ontbrekende koers vóór
  doeldatum, verouderde koers, ongeldige koers, ontbrekende verplichte features,
  ongeldige symbolen, providerstop en verwerkings-/databasefouten onderscheiden.
- Ontbrekende features worden ook per feature geteld. MA200 vereist voldoende
  historie; HIGH52W vereist 252 balken. De indicatorformules en vereiste velden
  zijn niet aangepast om de dekking kunstmatig te verhogen.
- Voortgang na iedere honderd tickers, ook als de honderdste wordt overgeslagen;
  ongebufferde workflowlogging. De oorspronkelijke voortgangsregel stond alleen
  op het pad voor een geslaagde opslag.
- Vijftien seconden timeout per yfinance-aanroep voor aandeel en referentie-index.
  Dit is geen harde timeout voor alle interne HTTP-verzoeken samen.
- Opgegooide en door yfinance alleen gelogde Yahoo-rate-limits stoppen verdere
  koersaanvragen. Geen automatische herstart of retrylus. Latere tickers krijgen
  `niet_opgevraagd`, de afgebroken aanvraag `afgebroken`; dat is niet hetzelfde
  als bewezen onbruikbare historie. Een providerstop maakt de run rood, ook als
  eerder al 80% van de tickers was opgeslagen.
- Telegram blijft behouden en bevat de redenaantallen. De CSV wordt met
  `if: always()` als Actions-artifact bewaard, ook bij onvoldoende dekking.

De bestaande invoer is de volledige unie van x-lijsten 041–059: op deze main
1000 unieke tickers. Er stond geen werkende beperking `MAX_TICKERS_PER_RUN` in
het script. De misleidende workflowsuggestie daarvoor is verwijderd; het
universum is niet verkleind. De minimumdekking blijft 80% en transacties blijven
per ticker; alleen na een geslaagde commit telt een rij als geschreven.

## Verificatie en vervolg

Elf offline regressies gebruiken synthetische historie, gemockte database en
gemockte notificaties: onvoldoende dekking, exacte skipredenen, ontbrekende
MA200/HIGH52W, lege en gedeeltelijke rate-limit-responsen, stoppen bij indexlimiet,
behoud van succesvolle commits na een fout, een rode providerstop na 80% opslag
en volledige geslaagde opslag.

Er is geen nieuwe live scan gestart en geen productiegegeven gewijzigd tijdens
de validatie. De oorzaakverdeling van de oorspronkelijke 554 skips blijft onbekend.
De volgende run moet die verdeling met het CSV-artifact aantonen; deze correctie
is geen bewijs dat de volgende run boven 80% zal uitkomen.
