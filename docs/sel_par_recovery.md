# Beperkt herstel van de dagelijkse sel_par-keten

Dit geldt uitsluitend voor Ronnyvdb2000/refactor. Fantasie en refactor-lab zijn
aparte repositories. Geen nieuwe tabellen, modellen of Telegraminstellingen.

## Dagelijkse pogingen

De bestaande refresh start om 04:45, 08:15, 12:15 en 16:15 UTC. In Belgische
wintertijd is dat 05:45, 09:15, 13:15 en 17:15; in zomertijd een uur later.
GitHub kan geplande starts vertragen. Elke poging verwerkt gisteren volgens UTC.
De laatste poging is geen garantie: een aanhoudende storing blijft zichtbaar als
mislukte Actions-run en vraagt ingrijpen.

Geplande pogingen wachten niet twee uur in een runner: actieve of ontbrekende
bronruns blokkeren direct en een volgende poging probeert opnieuw. Bestaande
rapportage van afgeronde maar mislukte strategieën blijft gehandhaafd; dit is
geen garantie dat iedere strategie succesvol was. Handmatig starten behoudt de
instelbare wachttijd (standaard 120 minuten).

Een bestaande zaai-registratie betekent niet dat de verrijking voltooid is.
Pas enrichment.complete_execution=true EN input_released=true in
sel_par_runs.resultaat betekenen dat een dag klaar is. Een gedeeltelijke
verrijking wordt opnieuw opgepakt met de bestaande per-aandeel voortgang.
Een afgeronde dag wordt niet opnieuw bij de dataprovider berekend; wel worden
de bestaande datum-, dekking- en herkomstcontroles opnieuw uitgevoerd en wordt
een nieuw manifest gepubliceerd. Hierdoor kan ook een gemiste workflow_run
bij een volgende poging alsnog scoring starten.

Een vrijgegeven dag is voor automatische herstelpogingen bevroren. Selecties die
pas daarna binnenkomen vragen een bewuste handmatige refresh met force=true.
Force wist de registratie van reeds afgeronde scoring niet.

## Eenmaal scoren per model en selectiedag

De drie directe consumenten (A Parameter-model, A Meta-model en XGBoostV3)
gebruiken bij workflow_run maintenance.score_once. De registratie staat onder
sel_par_runs.resultaat.scoring.a_par / a_meta / xgboost. De sleutel is de
selection_cutoff van het gecontroleerde manifest; de bestaande parameters en
horizons blijven behouden. Een sessie-advisory-lock voorkomt gelijktijdige
uitvoering van hetzelfde model. Registratie van verschillende modellen wordt
atomair samengevoegd en overschrijft elkaar niet.

Na exitcode 0 staat de status op completed. Herhaalde opvolgruns slaan dan de
modelaanroep en daarmee nieuwe berichten over. Een providerfout tijdens de
refresh schrijft deze scoringsmarker niet en kan normaal herstellen.

PostgreSQL en Telegram/mail vormen geen gezamenlijke transactie. Een storing
van de scorer kan dus optreden nadat een deel van de resultaten of een bericht
al is verstuurd. Daarom worden running of needs_review NIET blind herhaald.
Controleer het run_id, de logs, het modelarchief en ontvangen berichten. Een
bewuste workflow_dispatch van de scorer behoudt de bestaande handmatige
semantiek en omzeilt deze automatische dagbeveiliging; dit kan opnieuw berichten
versturen. Na gecontroleerd herstel kan een beheerder uitsluitend de betrokken
scoringstatus in het dagrapport corrigeren. Verwijder niet het hele dagrapport.
De eerste automatische uitvoering na invoering maakt de eerste dagmarker;
oudere runs zonder marker worden niet achteraf als voltooid aangenomen.

Dit is beperkt herstel via bestaande workflows. Het biedt geen onafhankelijke
scheduler, onbeperkte retries of automatische herhaling van een onzekere
modeluitvoering. De overige modelworkflows worden niet gewijzigd.
