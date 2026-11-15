# Controle workflowplanning — 15 november 2026

Repository: `Ronnyvdb2000/refactor`; uitgangspunt `ee79ee5`. De lopende forward-returnrun is niet onderbroken of opnieuw gestart. Weekly-workflows zijn geïnventariseerd, maar hun bestanden blijven ongewijzigd voor de controle in de andere chat. `refactor-lab` is niet benaderd.

## Resultaat

| Controlepunt | Bevinding | Herstel |
| --- | --- | --- |
| Inventarisatie | 66 workflowbestanden, waarvan vijf onder weekly-scope | Volledig [workflowoverzicht](../workflow_planning.md) met tijden, entrypoints, joblimieten, run-groepen en gelijke startminuten |
| Runbeveiliging | 40 workflows zonder workflow- of volledige jobbeveiliging | Beveiliging per workflow/branch toegevoegd; bestaande groepsnamen behouden |
| Wachtplaatsen | Bestaande guards hadden standaard één wachtplaats | Alle 61 workflows binnen scope gebruiken `queue: max`, met `cancel-in-progress: false` |
| Tijdsbegrenzing | 33 jobs zonder expliciete limiet | Ontbrekende limieten toegevoegd; bestaande limieten behouden |
| Oude YAML-verwijzingen | Drie push-filters verwezen naar niet-bestaande workflowpaden | Werkelijke bestandsnamen ingesteld bij parameterproductie, XGBoost V4 en TradingAgents Bridge |
| Gelijktijdige Git-publicatie | Tien niet-weekly workflows pushten rechtstreeks; vijf gebruikten conflictvoorkeur `-X ours` | Gedeelde publicatiehelper met fetch, rebase, gewone push en maximaal drie pogingen; bij conflict stoppen |

Een workflowguard voorkomt dat dezelfde workflow binnen zijn groep tegelijk draait. Verschillende groepen kunnen nog parallel draaien. Dit is geen repositorybreed Yahoo-verzoekbudget. Jobs binnen dezelfde workflow blijven hun bestaande `needs`- en `if`-voorwaarden volgen; bijvoorbeeld de handmatige EU/NA-superbots kunnen nog twee jobs starten.

`queue: max` bewaart volgens [GitHub's documentatie](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) maximaal honderd wachtende runs/jobs per groep. Dat is geen onbeperkte wachtrij en geen garantie van een data-afhankelijkheidsvolgorde. Een grote achterstand moet nog steeds worden onderzocht; scripts bepalen veelal hun datum bij uitvoering.

Ontbrekende scan-/trainlimieten zijn conservatief op 300 minuten gezet, niet op basis van een bewezen nieuwe snellere looptijd. Modusselectie krijgt vijf minuten; diagnose/patch/CSV-onderhoud 10–15 minuten, rapportanalyse 30 minuten, rank-backfill 90 minuten en de reeds uitgeschakelde VCP-bridgejob 90 minuten. Deze limieten begrenzen vastlopers, maar lossen trage providerverzoeken niet op.

## Git-publicatie

`python -m maintainance.push_generated_commit` publiceert een reeds door de workflow gemaakte commit. Staging, bestandsselectie en commitberichten blijven bij de bestaande workflow. De helper:

- weigert resterende niet-gecommitteerde wijzigingen aan getrackte bestanden;
- haalt `origin/main` opnieuw op en plaatst lokale commits daarop;
- gebruikt uitsluitend een gewone `HEAD:main`-push, zonder force;
- stopt bij een inhoudelijk conflict in plaats van automatisch de lokale of externe versie te kiezen;
- probeert na een geweigerde push maximaal drie keer opnieuw;
- weigert uitvoering met een andere `GITHUB_REPOSITORY` dan `Ronnyvdb2000/refactor`.

De helper wordt gebruikt door CAN SLIM, Dual Momentum, Kritische Selectie, Minervini, de twee oudere XGBoost-varianten, XGBoost V2/V3, tickerfiltering en de handmatige CSV-opruimer. Bestaande waarschuwingen/kwaliteitsvoorwaarden van deze jobs veranderen niet; de helper valideert geen modelkwaliteit of scanvolledigheid.

## Planning en afhankelijkheden die openblijven

De bestaande crons, invoerinstellingen, basisbotcommando's, secrets, notificatiekanalen en uitgeschakelde jobs blijven behouden. De drie herstelde self-pathfilters gaan pas bij een relevante latere push meedoen. De publicatiecommit gebruikt `[skip actions]`, zodat deze onderhoudsronde geen nieuwe automatische trainingen, LLM-aanvragen of notificatieruns start; handmatige en geplande runs blijven beschikbaar. Zie [GitHub's skip-documentatie](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs).

| Keten / piek | Huidige situatie (UTC) | Nodige vervolgcontrole |
| --- | --- | --- |
| Basisbots → Beste Signaal/Q | Combiners 19:15/19:20; veel basisbots vanaf 19:30 | Bepalen of het rapport bewust eerdere beschikbare signalen gebruikt of dezelfde avond moet afwachten. Een vroeger rapport is niet automatisch fout. |
| Technicals → XGBoost scoring | Technicals 21:00, scoring 21:30; technicals mag 180 minuten lopen | Datadatum, dekking en daadwerkelijke voltooiing als publicatievoorwaarde controleren. Dertig minuten op de klok is geen afhankelijkheidscontrole. |
| Fundamentals → parametermodel | Fundamentals 21:30 met maximaal 200 tickers/run; parameterproductie 05:00 | Controleren of dekking voldoende is, ook wanneer vorige runs een achterstand hebben. |
| Forward returns → modeltraining | Forward returns 22:00; zondag Ridge-training eveneens 22:00, XGBoost V3 23:00 | Training alleen laten doorgaan met een coherente gerijpte labelset. Nieuwste ongerijpte horizons hoeven niet gevuld te zijn. |
| Yahoo-piek 19:50 | Minervini, cointegratie, kasstroom en marktsentiment starten op dezelfde minuut | Werkelijke requestdruk/looptijden meten vóór een gezamenlijke queue of nieuwe spreiding wordt ingevoerd. |
| Andere avondpieken | 19:30, 19:45, 19:55, 20:00, 20:05 en 21:30 hebben meerdere starters | De volledige tabel bevat ook vrijdag/zondagpieken; verschillende startminuten sluiten looptijdoverlap niet uit. |
| Schrijven naar main | Meerdere model-/CSV-writers blijven onafhankelijk | Nieuwe helper voorkomt blind conflictvoorkeur; een echt artefactconflict blijft bewust een fout. |

Een globale Yahoo-wachtrij is nog niet ingevoerd: zonder gemeten doorlooptijden kan één zeer lange full-scan alle dagelijkse/momentgebonden scans ophouden. Losse concurrencygroepen lossen bronlimieten over workflows heen niet op. Weekly wordt hierbij alleen als andere bron van mogelijke belasting vermeld en niet gewijzigd.

## Herhaalbare controle

```bash
python -m pip install PyYAML
python -m maintainance.audit_workflows --format json
python -m maintainance.audit_workflows --format markdown > docs/workflow_planning.md
```

De audit leest uitsluitend bestanden. Hij controleert binnen scope entrypoints, runbeveiliging, wachtplaatsen, joblimieten en niet-bestaande self-pathfilters. Hij wijzigt geen workflow, dispatcht geen job en benadert geen database. Cronuitbreiding ondersteunt een generieke week; schema's met een vaste maand/dag krijgen een expliciete waarschuwing. De inventarisatie bewijst geen ingeschakelde GitHub-workflow of live succes.

## Verificatie

`python -m pytest tests/ -q`: 671 tests en 17 deeltests geslaagd, 12 optionele tests overgeslagen. De audit meldt geen fouten of waarschuwingen binnen de ondersteunde controles. De na de Markdown-uitbreiding opnieuw uitgevoerde 16 gerichte planning-/publicatietests slagen eveneens.

De semantische YAML-vergelijking met de uitgangsversie bevestigt dat alleen guards, ontbrekende joblimieten, drie push-paden en Git-publicatie zijn gewijzigd. Weekly-bestanden zijn bytegelijk aan de uitgangsversie; gedeactiveerde modellen en de gedeactiveerde VCP-bridge blijven uitgeschakeld.

Tests oefenen cronberekening, detectie van ontbrekende scripts/paden/limieten/guards, weekly-scope en bestaande modulecommando's. De publicatiehelper is uitgevoerd tegen geïsoleerde lokale bare Git-repositories: een gelijktijdige broncodecorrectie blijft behouden; een conflict tussen twee outputs stopt; niet-gecommitteerde broncode blokkeert publicatie; `refactor-lab` wordt geweigerd. Er zijn geen testpushes naar GitHub gedaan.
