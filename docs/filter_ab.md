# Proef A/B: huidige filter versus geen minimumvolatiliteit

A behoudt de huidige Noord-Amerikaanse criteria. B verandert uitsluitend
`VOL_MIN` van 0,35 naar 0. Alle andere drempels, sectoruitzonderingen en de
bestaande drie-wekentermijn voor verzwakkende aandelen blijven gelijk.
De lijsten zijn 048, 056 en 057. Proef C maakt geen deel uit van deze wijziging.

## Waarom nog geen historische rendementsuitslag?

De bestaande `sel_par`- en backtestgegevens bewaren vooral strategie-selecties.
Er is geen complete historische registratie van alle afgewezen bron-aandelen met
de toen waargenomen fundamentele invoer en filterredenen. Een actuele Yahoo
fundamentele beoordeling mag niet als een historische waarneming worden gebruikt.
Een bredere selectie backtesten met uitsluitend eerder doorgelaten aandelen zou
de centrale onderzoeksvraag niet beantwoorden.

## Wat nu automatisch wordt vastgelegd

De bestaande wekelijkse filter blijft zijn eigen actieve master- en exportlijsten
maken. Een aparte recorder bewaart dezelfde reeds opgehaalde Amerikaanse invoer,
de A- en B-beslissing, afwijzingen, ontbrekende gegevens, laatste koersdatum,
volatiliteit, handelsomzet, actuele technische kenmerken en gebruikte financiële
providerwaarden. B vraagt in die wekelijkse run geen extra koers- of
fundamentele downloads en schrijft niet naar productie-exportlijsten.

Iedere ronde bevriest de bronlijst, het protocol en de begintoestand. De eerste
B-toestand begint bij dezelfde master als A; latere volledige wekelijkse rondes
gebruiken de vorige B-toestand. Daarmee wordt de drie-wekenregeling ook voor B
doorgezet. Onbekende fundamentele invoer wordt overeenkomstig de bestaande
productielogica niet stilzwijgend goedgekeurd. Datakwaliteitsproblemen worden
geteld. Zowel de ruwe native beslissing als de betrouwbaarheid van de invoer
wordt bewaard: een door de bestaande filter toegepaste foutbeslissing krijgt in
beide replays dezelfde behandeling, maar wordt geen betrouwbare observatie.
De replay van A wordt met de werkelijke A-export vergeleken; een mismatch of een
ontbrekende waarneming maakt de ronde onvolledig. Alleen volledige wekelijkse
B-rondes worden als volgende begintoestand gebruikt. Een onvolledige wekelijkse
ronde blokkeert verdere toestandsoverdracht tot die dekking is hersteld.

De aparte workflow `Filterproef A-B - waarnemingen en dekking` begint met een
deterministisch, uit tickerhashes gekozen steekproef van 60 bron-aandelen: twintig
per lijst. Dit test de registratie op echte gegevens. De steekproef kan niets
bewijzen over het volledige universum en verandert de wekelijkse B-toestand niet.
Daarna meldt de workflow de resultaten van de bestaande wekelijkse filterronde.
Iedere melding noemt de dekking, A/B-aantallen en extra B-aandelen.

Opslag staat in private `research_filter_ab_*`-tabellen, met RLS en ingetrokken
publieke rechten. Een observatie wordt eenmaal vastgelegd en nooit bij een
herstart vervangen. Telegramclaims worden vóór verzending gecommit; een
onzekere bezorging wordt niet automatisch opnieuw verstuurd.

## Grens van deze fase en volgende vereiste

Dit is de universe- en dataverzamelingsfase, geen afgeronde A/B-portefeuilleproef.
`portfolio_ready=false` en `return_pct=null` zijn expliciet in de rapportage.
Er worden nog geen A/B-orders, modelpicks of fictieve winsten geproduceerd.

Voor de rendementsfase moeten de ongewijzigde strategieën ook op de nieuwe
B-aandelen worden toegepast, gevolgd door dezelfde parameterwegingen en
uitvoeringsregels: tien plaatsen, $100.000, 10 handelsdagen, vaste cashplaatsen,
5 bp commissie en 10 bp slippage per zijde. Die vergelijkbare strategie-invoer
en de toekomstige uitvoeringskoersen ontbreken nu. B-aandelen rechtstreeks aan
de model-top-10 toevoegen zou óók de strategievoorselectie veranderen en is
daarom niet toegestaan als ongemelde vervanging van deze proef.

Na voldoende waarnemingen worden netto rendement, terugval, turnover en
kapitaalblootstelling op dezelfde perioden beoordeeld. De huidige backtests en
paperproeven blijven zelfstandig bestaan.
