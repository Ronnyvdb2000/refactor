# Ontbrekende koersen en cashplaatsen

Bij tien geplande aandelen en $100.000 startkapitaal blijft iedere plaats $10.000. Eén of twee onbruikbare instapkoersen leveren $90.000 of $80.000 belegd kapitaal op. De overige plaatsen blijven cash; er worden geen vervangers geselecteerd. Rendement wordt over het volledige kapitaal inclusief cash berekend. De kandidaatbaseline behoudt eveneens haar oorspronkelijke aantal plaatsen.

De historische reconstructie slaat aandelen met onvolledige koersen in de verstreken periode over en rapporteert de uitsluitingen per periode. Dit gebruikt achteraf bekende koersbeschikbaarheid en kan vertekening geven; de rapportage benoemt dit expliciet. Gebruik hiervoor een nieuwe backtestrun, want de configuratie bevat de codehash.

De vooruitblik gebruikt alleen instapgegevens om plaatsen over te slaan. Die beslissing blijft bewaard als de provider later koersen aanvult. Individuele providerfouten verhinderen het ophalen van andere aandelen niet. Ontbreekt een koers na aankoop, dan blijft het aandeel een bestaande, onopgeloste positie. Er wordt geen verkoop of winst verzonnen: de rapportage blijft wachten op deze koers, terwijl de workflow normaal eindigt en de volgende run opnieuw probeert. Splits blijven een afzonderlijke controle vereisen.

Dit gewijzigde protocol start afzonderlijk als paper_10d_usd_v2. De workflow initialiseert het automatisch. De bestaande v1 met DV, BOW en acht andere aandelen blijft ongewijzigd opgeslagen; de nieuwe workflow volgt v2. De resultaten van beide protocollen worden niet gecombineerd. Telegram vermeldt bij gesloten perioden het belegde bedrag, aantal posities en overgeslagen aandelen.
