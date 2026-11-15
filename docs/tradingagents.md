# TradingAgents Bridge — controle 15 november 2026

De actieve workflow is `.github/workflows/tradingagentsbridge.yml`. De losse TradingAgents-job onder `run_00vcp.yml` blijft uitgeschakeld. Telegram blijft beschikbaar in de actieve bridge.

## Gevonden oorzaak

Run [43667889791](https://github.com/Ronnyvdb2000/refactor/actions/runs/43667889791) startte correct, maar eindigde na ongeveer 25 minuten met nul geslaagde analyses. Groq gaf 413 (te grote prompts), 429 TPM en uiteindelijk 429 TPD. Dat was geen YAML-parsefout. De opgebruikte daglimiet wordt niet opgelost door de run groen te kleuren.

Daarnaast bevatte de workflow uitvoeringsfouten:

- De bron viel bij een push terug op `par`, maar de stap die par-picks maakt werd dan overgeslagen.
- De workflow forceerde een ander standaardmodel dan de Python-bridge; de oude Llama-standaard en Qwen 3.6-keuze zijn volgens Groq stopgezet voor free/developer-gebruik.
- Eerst pandas 2.2.3 en requests 2.32.3 vastzetten, daarna de actuele TradingAgents installeren, leverde geen gezamenlijke dependencycontrole op. TradingAgents vereist inmiddels pandas >=3.0.6 en requests >=2.32.4.
- `online_tools=False` wordt niet meer gebruikt door de geïnstalleerde TradingAgents-versie en schakelde online brondata dus niet uit.
- VCP kon in deze afzonderlijke runner niet bij het signalenbestand van de bronjob.
- Een fout in het par-model kon door de afsluitende `tee` worden gemaskeerd.

## Aanpassing

De workflow normaliseert bron, horizon, aantal, kanaal en datum eenmaal via omgevingsvariabelen. Handmatige invoer wordt gevalideerd en shellargumenten gebruiken arrays. Een opgegeven datum gaat naar zowel het par-model als de bridge. Ontbrekende par-picks stoppen expliciet. VCP haalt het artifact `vcp-signals` uit de laatste geslaagde bronrun op main; ontbrekende/verlopen artifacts geven een fout.

Beide standaardmodelinstellingen zijn `openai/gpt-oss-20b`; 120b en Qwen 3.8 blijven handmatig selecteerbaar. De standaardselectie daalt van tien naar vijf tickers. `--top-n` geldt nu voor beide bronnen. De bridge begrenst toolrondes op één, ticker-nieuws op drie artikelen, globaal nieuws op twee en output op 1024 tokens. Verborgen SDK-retries zijn uitgeschakeld; de bestaande bridge-retrylogica blijft zichtbaar in het log. Dit beperkt verbruik, maar garandeert geen resterend providerbudget of geslaagde analyse.

`requirements-tradingagents.txt` lost de dependencies gezamenlijk op en fixeert TradingAgents op commit `9fa1196fffbcfd9de5146d2dd2869df84ab99838`, die ook in de onderzochte runner geïnstalleerd was. Een pip-cache gebruikt deze dependencylijst als sleutel. De broninstellingen voor online nieuws blijven actief: deze wijziging pretendeert geen volledig offline analyse.

## Verificatie

De tests voeren de workflow-shellblokken uit met ongeldige invoer, ontbrekende picks en nagebootste Python-processen. Ze controleren datumdoorgifte, foutpropagatie door `tee`, de limiet voor VCP en Telegram bij een Groq-daglimiet. Er worden geen LLM-calls gedaan en geen berichten verstuurd tijdens tests.

Actionlint 1.7.12 controleert de workflow met uitsluitend zijn verouderde melding over `queue: max` genegeerd. Die optie blijft behouden: GitHub ondersteunt ze sinds mei 2026 en de eerdere run passeerde de workflowvalidatie. Alle shellblokken worden ook met `bash -n` gecontroleerd.

Bronnen: [Groq-modelvervanging](https://console.groq.com/docs/deprecations), [GPT-OSS 20B](https://console.groq.com/docs/model/openai/gpt-oss-20b), [TradingAgents-configuratie op de vastgezette commit](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/default_config.py), [GitHub concurrency-wachtrijen](https://github.blog/changelog/2026-05-07-github-actions-concurrency-groups-now-allow-larger-queues/).


Validatie: 24 gerichte workflow-/bridge-/planningtests geslaagd; volledige suite 761 tests en 17 deeltests geslaagd, 9 verbindingstests overgeslagen. De gezamenlijke dependencies zijn daadwerkelijk geïnstalleerd in een aparte verificatieomgeving; TradingAgents 0.7.1, de graph-import, `--help` en het construeren van de grafiek met deze configuratie slagen. De lokale verificatie gebruikte Python 3.12; de Actions-run controleert Python 3.11. Er is lokaal geen `propagate()` of echte LLM-analyse uitgevoerd. De volgende productierun moet beschikbaarheid en het actuele Groq-budget nog bevestigen.
