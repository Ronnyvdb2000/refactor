# Onderzoeksmandjes bewaren en opvolgen

De simulator bewaart voortaan het volledige mandje vóór Telegram-verzending:
signalen, aantallen, referentiekoersen, instellingen, bronbeschrijving en
codevingerafdrukken. Dit is een afzonderlijke praktijkproef; er zijn geen orders.

## Opslag

```bash
python -m research --supabase --archive --output results/research_basket.json --telegram
```

De workflow [research_simulation.yml](../.github/workflows/research_simulation.yml)
blijft dagelijks om 07:17 UTC draaien. `--archive` maakt zo nodig
`public.research_basket_log` en schrijft daar met een gewone transactie één
JSON-snapshot per uniek mandje. Vereiste rechten: CREATE voor de eerste tabel,
INSERT en SELECT voor het logboek. De bronadapter blijft alleen-lezen; bestaande
selecties, modellen en rendementsgegevens worden niet aangepast.

De sleutel volgt valuta, signalen inclusief hun brondata, beleid en posities.
Een identieke herhaling houdt de oorspronkelijke inhoud en het eerste
databasevastleggingstijdstip. Alleen een nieuwe codecommit maakt een ongewijzigd
mandje niet nieuw. Gewijzigde signalen of instellingen leveren wel een apart
mandje op. Er is geen UPDATE-route; een databasebeheerder kan de tabel uiteraard
wel aanpassen. Dit is geen onveranderbaar extern archief.

Mislukt opslag, dan wordt geen succesvol simulatiebericht verstuurd. Mislukt
Telegram ná opslag, dan blijft het mandje behouden. Het vastleggingstijdstip is
dus geen ontvangstbevestiging van Telegram. De eerste vastlegging verschijnt in
het bericht. Een JSON-kopie wordt als Actions-artifact 90 dagen bewaard; het
Supabase-logboek wordt niet automatisch verwijderd. Er wordt niets naar Git gepusht.

Oude berichten en mandjes van vóór deze wijziging worden niet automatisch
achteraf voorzien van een verzonnen vastleggingstijd. De registratie begint
bij de eerste succesvolle uitvoering met `--archive`.

## Meetregels

De workflow [research_evaluation.yml](../.github/workflows/research_evaluation.yml)
draait dagelijks om 07:47 UTC (08:47 Belgische wintertijd, 09:47 zomertijd),
en kan handmatig gestart worden:

```bash
python -m research.evaluate --telegram
```

De evaluator leest het logboek in een alleen-lezen transactie. Ontbreekt de tabel
nog, dan rapporteert hij nul opgeslagen mandjes. Hij gebruikt de gedeelde
Yahoo-featurewachtrij en doet koersaanvragen serieel, eenmaal per ticker per run.
JSON en Markdown bevatten alle mandjes en individuele uitkomsten; artifacts
blijven 90 dagen beschikbaar. Evaluatieresultaten worden opnieuw berekend, niet
als nieuwe voorspellingen opgeslagen.

- Instap: aangepaste Open van de eerste beschikbare sessie **strikt na de
  UTC-vastleggingsdag**. Ook wanneer het bericht vóór de Amerikaanse opening
  verscheen, begint de conservatieve proef pas op een latere dag.
- Uitstap: aangepaste Close van sessie 10, 30 of 60, inclusief instapsessie.
  De lopende UTC-dag telt nooit mee. Referentiekoersen uit het originele
  selectiebericht worden niet als uitvoeringskoers gebruikt.
- Aantallen blijven vast. Aankoop en verkoop krijgen de vastgelegde ongunstige
  slippage en elk hun eigen minimum/procentcommissie. Rendement is netto P&L
  gedeeld door het volledige fictieve startkapitaal; ongebruikte cash rendeert nul.
- Is het mandje door een hogere openingskoers niet betaalbaar, dan krijgt het
  `entry_budget_exceeded`, geen fictieve lening of ongemerkte aanpassing van aantallen.
- De vergelijking gebruikt dezelfde aandelen met een positieve toegewezen
  positie, hetzelfde totale instapbedrag, dezelfde kosten en dezelfde datums.
  Gelijk wegen gebeurt met fractionele aantallen als analytische comparator;
  dat is geen uitvoerbaar mandje met uitsluitend hele aandelen.
- Een mandje telt pas als volledig als alle posities volgroeide, geldige koersen
  en dezelfde instap/uitstapdatums hebben. Providerfouten, ontbrekende koersen,
  onrijpe horizons en lange koersgaten blijven expliciet onvolledig. Ze worden
  niet weggefilterd om alleen overlevers te vergelijken of als nulrendement geteld.

## Grenzen

Dit zijn vaste meettermijnen: de eerder berekende stops worden **niet uitgevoerd**
in deze evaluatie. Er is geen doorlopende portefeuille, herbelegging, indexbenchmark
of portefeuille-drawdown. De comparator onderzoekt positieweging, niet of de
oorspronkelijke aandelenkeuze beter is dan de markt.

Alleen USD-mandjes worden geëvalueerd; geen FX-omrekening. Yahoo's aangepaste
koersen zijn geen brokerfills of volledige administratie van splits/dividenden.
Zonder officiële beurskalender zijn korte koersgaten en delistings niet volledig
te onderscheiden van normale sluitingsdagen. Resultaten kunnen bij
providerherzieningen veranderen; de oorspronkelijke mandjes blijven bewaard.

Mandjes en 10/30/60-dagenperioden overlappen. Hun rendementen worden niet bij
elkaar opgeteld tot één portefeuille of als onafhankelijke bewijzen gepresenteerd.
Netto beter dan gelijk gewogen op enkele mandjes bewijst geen blijvend voordeel.
