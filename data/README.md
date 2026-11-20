# Tickerbestanden

Alle `tickers_*.txt`-bestanden staan in deze map, met hun bestaande namen en inhoud.

| Naam | Gebruik |
| --- | --- |
| `tickers_00.txt` t/m `tickers_09.txt` | Overige/thematische universums; afnemers bepalen hun selectie |
| `tickers_041a.txt` e.v. | Bronlijsten voor filtering en scanners |
| `tickers_041m.txt` e.v. | Filter-masterlijsten met historie en parameters |
| `tickers_041x.txt` e.v. | Actieve selectielijsten |
| `tickers_041d.txt` e.v. | Delisted-cache met bestaande herprobeerregels |

Start bots en scripts vanuit de repository-root. `common.io.bouw_bestandslijst` geeft `data/tickers_…` terug. De gedeelde lader accepteert ook expliciete bestandsnamen en absolute paden; hij zoekt niet stilzwijgend naar oude bestanden in de root. `label_voor` bepaalt de beurs op basis van de bestandsnaam, zodat het mapvoorvoegsel geen invloed heeft.

Weekly leest eveneens uit `data/`, maar behoudt de kale bestandsnamen als interne lijstidentiteit voor beurscodes, aliassen en dekkingscontroles. De MRA-filter leest en schrijft alle a/m/x/d-varianten in deze map. Zijn workflow neemt broncorrecties en gegenereerde lijsten gezamenlijk mee bij publicatie. De verplaatsing verandert geen tickerselecties, suffixregels of filtercriteria.
