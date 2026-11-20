# Opschoning main — 20 november 2026

Basiscommit: `8e28657c37e264bbdd473c9e3eb1511dffbc32cd`. Broncode-inventarisatie, geen verklaring dat iedere geplande run live slaagt.

## Bevindingen en uitgevoerde correcties

De basis bevatte 150 Python-bestanden, waarvan 27 in de hoofdmap, en 66 workflowbestanden. De mapindeling voor basisstrategieën, combiners, weekly, meta-modellen, analyses en maintenance is al aanwezig. `a_par_model.py` staat uitsluitend in `meta_models/`; de eerdere losse analysebestanden zijn naar `analyses/` verhuisd. Er zijn geen CSV-bestanden in deze checkout opgenomen.

| Bevinding | Correctie |
| --- | --- |
| `test_verbinding.py` verstuurde bij import meteen een Telegram-bericht; `test_telegram.py` was als pytest-test te ontdekken | Beide vervangen door de expliciete CLI `maintenance.check_telegram` |
| Handmatige tests vertrouwden op logtekst en hadden geen HTTP-timeout | Statuscodes, API-bevestiging, timeout van 20 seconden en exitcode 1 bij fouten; workflowkeuzes behouden |
| Koersdiagnose startte Yahoo-downloads bij import en stond los in de hoofdmap | Verplaatst naar `maintenance.diag_koersen`, main-guard en configureerbare begindatum |
| Dagelijkse research-startworkflow miste de vereiste meerdere wachtplaatsen | `queue: max` toegevoegd; tijdstip en branchcheckout behouden |
| Workflow-audit meldde onderzoekscode uit een afwijkende checkout als ontbrekend op main | Afwijkende checkouts afzonderlijk geregistreerd, zonder lokale validatie te claimen; bestaande lokale controles blijven actief |
| Gegenereerd workflowoverzicht liep achter op de code | Opnieuw gegenereerd |

## Uitvoer en resterende inventarisatie

De bestaande `common.exports` zet optionele CSV-rapporten standaard uit. Resterende directe schrijvers zijn nog niet automatisch verwijderd, omdat de afnemer per geval verschilt:

| Groep | Voorbeelden | Beoordeling voor vervolg |
| --- | --- | --- |
| Provider-/labeldiagnostiek | `bouw_generieke_technicals.py`, `bouw_forward_returns.py` | CSV-dekkingsrapporten worden door workflows als artifacts gebruikt |
| Expliciete CLI-export | `analyses/a_top_dist_sma50.py`, `analyse_selecties.py`, `analyseer_forward_correlaties.py` | Alleen met exportargument; standaardgedrag en workflowargumenten samen beoordelen |
| Model- en analyserapporten | `xLightgbmV2.py`, `analyses/analyse_parameter_ic.py`, vergelijkingsscripts in `meta_models/` | Nog directe CSV-rapporten; omzetting naar JSON vereist ook aanpassing van artifactpaden en eventuele lezers |
| Weekly | `weekly/checkpoint.py`, `weekly/shared.py`, weekly-correlaties | Checkpoints zijn hervatbare invoer; overige dekkingsrapporten afzonderlijk beoordelen |

Root bevat nog builders, modelfuncties, trainingsscripts en offline tests. Een verdere verhuizing vraagt een eigen wijziging van imports, modelpaden, modelpublicatie en workflowcommando's. Er is in deze ronde geen modelbestand of data verwijderd.

## Verificatie en grenzen

26 gerichte offline tests geslaagd: handmatige controles, foutpaden, importveiligheid en workflowplanning. Alle 66 workflows geïnventariseerd: geen fouten of waarschuwingen in de lokale audit. Afwijkende checkouts worden expliciet apart vermeld. Python-syntax en de YAML/shell-syntax van de drie aangepaste workflows gecontroleerd. Geen live Telegram-tests of koersdownloads gestart. Geen volledige strategiesuite of volledige pytest-collectie uitgevoerd.

De Quantify-onderzoekscode, TradingAgents, refactor-lab, selectielogica en database-inhoud zijn niet gewijzigd. Supabase blijft voor de dagelijkse onderzoekssimulatie alleen-lezen.
