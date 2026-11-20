# Workflowplanning refactor

Alle tijden zijn UTC. Dit is broncode-inventarisatie; een cron bewijst geen ingeschakelde workflow of succesvolle live run.
Workflowguards begrenzen herstarts; de afzonderlijke Yahoo-jobgroepen delen dagelijkse, lange, feature- en tijdgebonden taken. Weekly valt buiten deze herstelronde.

| Workflow | Cron (UTC) | Python-entrypoints | Joblimiet (min) | Run-groep | Yahoo-jobgroep | Scope |
| --- | --- | --- | --- | --- | --- | --- |
| [a__meta_model_production.yml](../.github/workflows/a__meta_model_production.yml) | `0 23 * * 1-5`<br>`0 22 * * 0` | `meta_models/a_meta_model_train.py`, `meta_models/a_meta_model_score.py` | 20 | meta-model-production | Niet toegewezen | repo-planning |
| [a__parameter_model_production.yml](../.github/workflows/a__parameter_model_production.yml) | `0 5 * * *` | `meta_models/a_par_model.py` | 30 | workflow-a__parameter_model_production-${{ github.ref }} | Niet toegewezen | repo-planning |
| [a_meta_labeling_classifier.yml](../.github/workflows/a_meta_labeling_classifier.yml) | `0 5 * * 0` | `meta_models/a_meta_label_classifier.py` | 30 | meta-label-classifier | Niet toegewezen | repo-planning |
| [a_meta_model.yml](../.github/workflows/a_meta_model.yml) | `30 22 * * 1-5` | `meta_models/a_meta_model.py` | 20 | meta-model | Niet toegewezen | repo-planning |
| [a_trade.yml](../.github/workflows/a_trade.yml) | `15 19 * * *` | `combiners/a_trade.py` | 30 | ${{ github.workflow }}-${{ github.ref }} | Niet toegewezen | repo-planning |
| [a_trade_combi.yml](../.github/workflows/a_trade_combi.yml) | `0 5 * * *` | `combiners/a_trade_combi.py` | 240 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [a_trade_q.yml](../.github/workflows/a_trade_q.yml) | `20 19 * * *` | `combiners/a_trade_q.py` | 30 | ${{ github.workflow }}-${{ github.ref }} | Niet toegewezen | repo-planning |
| [a_voorl.yml](../.github/workflows/a_voorl.yml) | Handmatig/event | `analyses/analyse_forward_returns.py` | 15 | workflow-a_voorl-${{ github.ref }} | Niet toegewezen | repo-planning |
| [analyse_correlatie_parameters.yml](../.github/workflows/analyse_correlatie_parameters.yml) | `0 22 * * 1-5` | `analyses/analyse_parameter_correlatie.py` | 20 | analyse-parameter-correlatie | Niet toegewezen | repo-planning |
| [analyse_correlaties_weeklytop.yml](../.github/workflows/analyse_correlaties_weeklytop.yml) | Handmatig/event | `weekly/analyseer_weekly_correlaties.py` | 90 | ontbreekt | Niet toegewezen | weekly-apart |
| [analyse_forward_correlaties.yml](../.github/workflows/analyse_forward_correlaties.yml) | `0 0 * * 6` | `analyses/analyseer_forward_correlaties.py` | 30 | workflow-analyse_forward_correlaties-${{ github.ref }} | refactor-yahoo-features | repo-planning |
| [analyse_parameter_ic.yml](../.github/workflows/analyse_parameter_ic.yml) | `0 5 * * 0` | `analyses/analyse_parameter_ic.py` | 15 | workflow-analyse_parameter_ic-${{ github.ref }} | Niet toegewezen | repo-planning |
| [backfill.yml](../.github/workflows/backfill.yml) | Handmatig/event | `maintenance/backfill_technische_features.py` | 180 | backfill-technische-features | refactor-yahoo-full | repo-planning |
| [backfill_rank_kolommen.yml](../.github/workflows/backfill_rank_kolommen.yml) | Handmatig/event | `maintenance/backfill_rank_kolommen.py` | 90 | workflow-backfill_rank_kolommen-${{ github.ref }} | Niet toegewezen | repo-planning |
| [diag_koersen.yml](../.github/workflows/diag_koersen.yml) | Handmatig/event | `maintenance/diag_koersen.py` | 15 | workflow-diag_koersen-${{ github.ref }} | refactor-yahoo-features | repo-planning |
| [lightgbmV2_gecontroleerd.yml](../.github/workflows/lightgbmV2_gecontroleerd.yml) | Handmatig/event | `xLightgbmV2_gecontroleerd.py` | 60 | workflow-lightgbmv2_gecontroleerd-${{ github.ref }} | Niet toegewezen | repo-planning |
| [patch_xgboost.yml](../.github/workflows/patch_xgboost.yml) | Handmatig/event | `patch_xgboost_quality.py` | 10 | workflow-patch_xgboost-${{ github.ref }} | Niet toegewezen | repo-planning |
| [research_simulation.yml](../.github/workflows/research_simulation.yml) | `17 7 * * *` | `research.py` | 5 | research-usd-refs/heads/feature/quantify-principles | Niet toegewezen | repo-planning |
| [run_00cs.yml](../.github/workflows/run_00cs.yml) | `00 20 * * 1-5` | `strats/bot_00cs.py` | 300 | workflow-run_00cs-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00db.yml](../.github/workflows/run_00db.yml) | `55 19 * * 1-5` | `strats/bot_00db.py` | 300 | workflow-run_00db-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00dm.yml](../.github/workflows/run_00dm.yml) | `40 19 * * 1-5` | `strats/bot_00dm.py` | 300 | workflow-run_00dm-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00fisher.yml](../.github/workflows/run_00fisher.yml) | `0 22 * * 5` | `strats/bot_00Fisher.py` | 300 | workflow-run_00fisher-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00graham.yml](../.github/workflows/run_00graham.yml) | `0 20 * * 1-5` | `strats/bot_00graham.py` | 300 | workflow-run_00graham-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00greenblatt.yml](../.github/workflows/run_00greenblatt.yml) | `10 22 * * 5` | `strats/bot_00greenblatt.py` | 300 | workflow-run_00greenblatt-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00kr.yml](../.github/workflows/run_00kr.yml) | `45 19 * * 1-5` | `strats/bot_00kr.py` | 300 | workflow-run_00kr-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00mail.yml](../.github/workflows/run_00mail.yml) | `30 19 * * 1-5` | `strats/bot_00mail.py` | 300 | workflow-run_00mail-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00mrEOD.yml](../.github/workflows/run_00mrEOD.yml) | `30 19 * * 1-5` | `strats/bot_00mr.py` | 300 | mr-eod-scan | refactor-yahoo-daily | repo-planning |
| [run_00mrORB.yml](../.github/workflows/run_00mrORB.yml) | `0 10 * * 1-5`<br>`30 15 * * 1-5` | `strats/bot_00mr.py` | 300 | mr-orb-scan | refactor-yahoo-timed | repo-planning |
| [run_00ms.yml](../.github/workflows/run_00ms.yml) | `50 19 * * 1-5` | `strats/bot_00ms.py` | 300 | workflow-run_00ms-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00oshaughnessy.yml](../.github/workflows/run_00oshaughnessy.yml) | `20 22 * * 5` | `strats/bot_00oshaughnessy.py` | 300 | workflow-run_00oshaughnessy-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00supervar.yml](../.github/workflows/run_00supervar.yml) | `30 6 * * 1-5`<br>`15 13 * * 1-5` | `strats/bot_00supervar.py` | 300, 300 | workflow-run_00supervar-${{ github.ref }} | refactor-yahoo-timed, refactor-yahoo-timed | repo-planning |
| [run_00ultmail.yml](../.github/workflows/run_00ultmail.yml) | `45 19 * * 1-5` | `strats/bot_00ultmail.py` | 300 | workflow-run_00ultmail-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_00ultxxxV2.yml](../.github/workflows/run_00ultxxxV2.yml) | `30 20 * * 1-5` | `strats/bot_00xxxV2.py` | 300 | scanner-triplex-run | refactor-yahoo-daily | repo-planning |
| [run_00vcp.yml](../.github/workflows/run_00vcp.yml) | `05 20 * * 1-5` | `strats/bot_00vcp.py`, `tradingagents_bridge.py` | 300, 90 | workflow-run_00vcp-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_01beurssign.yml](../.github/workflows/run_01beurssign.yml) | `5 20 * * 1-5` | `strats/bot_01beurssign.py` | 90 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_01beurssignfull.yml](../.github/workflows/run_01beurssignfull.yml) | `40 22 * * 5` | `strats/bot_01beurssign.py` | 300 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-full | repo-planning |
| [run_01cointr.yml](../.github/workflows/run_01cointr.yml) | `50 19 * * 1-5` | `strats/bot_01cointegr.py` | 300 | ${{ github.workflow }}-${{ github.ref }} | ${{ github.event.inputs.mode == 'backtest' && 'refactor-yahoo-full' || 'refactor-yahoo-daily' }} | repo-planning |
| [run_01hoogl.yml](../.github/workflows/run_01hoogl.yml) | `55 19 * * 1-5` | `strats/bot_01hoogl.py` | 180 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_01hoogl_full.yml](../.github/workflows/run_01hoogl_full.yml) | `0 23 * * 5` | `strats/bot_01hoogl.py` | 300 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-full | repo-planning |
| [run_01kasstr.yml](../.github/workflows/run_01kasstr.yml) | `50 19 * * 1-5` | `strats/bot_01kasstr.py` | 180 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_01marktsent.yml](../.github/workflows/run_01marktsent.yml) | `50 19 * * 1-5` | `strats/bot_01marktsent.py` | 180 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_01repititief.yml](../.github/workflows/run_01repititief.yml) | `15 7 * * 1-5` | `strats/bot_01repititief.py` | 180 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-daily | repo-planning |
| [run_01volatieledipscan.yml](../.github/workflows/run_01volatieledipscan.yml) | `10 23 * * 5` | `strats/bot_01volhunter.py` | 240 | ${{ github.workflow }}-${{ github.ref }} | refactor-yahoo-full | repo-planning |
| [run_02XLightGBM V2 Trading.yml](../.github/workflows/run_02XLightGBM%20V2%20Trading.yml) | Handmatig/event | `xLightgbmV2.py` | 45 | workflow-run_02xlightgbm-v2-trading-${{ github.ref }} | Niet toegewezen | repo-planning |
| [run_02xgboost.yml](../.github/workflows/run_02xgboost.yml) | Handmatig/event | `strats/bot_01xgboost.py`, `maintenance/push_generated_commit.py` | 5, 300, 300, 300 | workflow-run_02xgboost-${{ github.ref }} | refactor-yahoo-daily, refactor-yahoo-full, refactor-yahoo-full | repo-planning |
| [run_02xgboostMeta.yml](../.github/workflows/run_02xgboostMeta.yml) | Handmatig/event | `strats/bot_01xgboostMeta.py`, `maintenance/push_generated_commit.py` | 5, 30, 60, 60 | workflow-run_02xgboostmeta-${{ github.ref }} | Niet toegewezen | repo-planning |
| [run_02xgboostV2.yml](../.github/workflows/run_02xgboostV2.yml) | `0 2 * * 0` | `xgboostV2.py`, `maintenance/push_generated_commit.py` | 300 | workflow-run_02xgboostv2-${{ github.ref }} | Niet toegewezen | repo-planning |
| [run_02xgboostV3.yml](../.github/workflows/run_02xgboostV3.yml) | `0 23 * * *` | `xgboostV3.py`, `maintenance/push_generated_commit.py` | 300 | workflow-run_02xgboostv3-${{ github.ref }} | Niet toegewezen | repo-planning |
| [run_02xgboostV4.yml](../.github/workflows/run_02xgboostV4.yml) | `0 4 * * 0` | `xgboostV4.py` | 45 | workflow-run_02xgboostv4-${{ github.ref }} | Niet toegewezen | repo-planning |
| [run__filtertickersmungerV2.yml](../.github/workflows/run__filtertickersmungerV2.yml) | `0 2 * * 0` | `scripts/bot_041mV2.py`, `maintenance/push_generated_commit.py` | 300 | workflow-run__filtertickersmungerv2-${{ github.ref }} | refactor-yahoo-full | repo-planning |
| [run_analyse_selecties.yml](../.github/workflows/run_analyse_selecties.yml) | `0 8 * * 6` | `analyses/analyse_selecties.py` | 30 | workflow-run_analyse_selecties-${{ github.ref }} | refactor-yahoo-features | repo-planning |
| [run_forward_returns.yml](../.github/workflows/run_forward_returns.yml) | `0 22 * * *` | `bouw_forward_returns.py` | 340 | forward-returns-${{ github.ref }} | refactor-yahoo-features | repo-planning |
| [run_generieke_fundamentals.yml](../.github/workflows/run_generieke_fundamentals.yml) | `30 21 * * *` | `bouw_generieke_fundamentals.py` | 90 | workflow-run_generieke_fundamentals-${{ github.ref }} | refactor-yahoo-features | repo-planning |
| [run_generieke_technicals.yml](../.github/workflows/run_generieke_technicals.yml) | `0 21 * * *` | `bouw_generieke_technicals.py` | 180 | workflow-run_generieke_technicals-${{ github.ref }} | refactor-yahoo-features | repo-planning |
| [run_top_dist_sma50.yml](../.github/workflows/run_top_dist_sma50.yml) | Handmatig/event | `analyses/a_top_dist_sma50.py` | 15 | workflow-run_top_dist_sma50-${{ github.ref }} | Niet toegewezen | repo-planning |
| [score_xgboost.yml](../.github/workflows/score_xgboost.yml) | `30 21 * * 1-5` | `score_xgboost.py` | 30 | workflow-score_xgboost-${{ github.ref }} | Niet toegewezen | repo-planning |
| [test_telegram.yml](../.github/workflows/test_telegram.yml) | Handmatig/event | `maintenance/check_telegram.py` | 5 | workflow-test_telegram-${{ github.ref }} | Niet toegewezen | repo-planning |
| [tradingagentsbridge.yml](../.github/workflows/tradingagentsbridge.yml) | `0 23 * * *` | `meta_models/a_par_model.py`, `tradingagents_bridge.py` | 90 | workflow-tradingagentsbridge-${{ github.ref }} | Niet toegewezen | repo-planning |
| [vergelijk_meta_par_10d.yml](../.github/workflows/vergelijk_meta_par_10d.yml) | Handmatig/event | `meta_models/vergelijk_meta_par_10d.py` | 40 | workflow-vergelijk_meta_par_10d-${{ github.ref }} | Niet toegewezen | repo-planning |
| [vergelijk_meta_par_horizons.yml](../.github/workflows/vergelijk_meta_par_horizons.yml) | Handmatig/event | `meta_models/vergelijk_meta_par_horizons.py` | 45 | workflow-vergelijk_meta_par_horizons-${{ github.ref }} | Niet toegewezen | repo-planning |
| [vergelijk_meta_par_horizons_diagnose.yml](../.github/workflows/vergelijk_meta_par_horizons_diagnose.yml) | Handmatig/event | `meta_models/vergelijk_meta_par_horizons_diagnose.py` | 45 | workflow-vergelijk_meta_par_horizons_diagnose-${{ github.ref }} | Niet toegewezen | repo-planning |
| [verwijdercsv.yml](../.github/workflows/verwijdercsv.yml) | Handmatig/event | `maintenance/push_generated_commit.py` | 15 | workflow-verwijdercsv-${{ github.ref }} | Niet toegewezen | repo-planning |
| [weekly_run.yml](../.github/workflows/weekly_run.yml) | `0 23 * * 5` | `weekly/weekly_report.py` | 90 | weekly-json-main | Niet toegewezen | weekly-apart |
| [weekly_runV2.yml](../.github/workflows/weekly_runV2.yml) | Handmatig/event | `weekly/weekly_rep_backtest.py` | 90 | weekly-backtest | Niet toegewezen | weekly-apart |
| [weekly_run_x.yml](../.github/workflows/weekly_run_x.yml) | `15 23 * * 5` | `weekly/weekly_report_x.py` | 90 | weekly-json-main | Niet toegewezen | weekly-apart |
| [xgboostV3_gecontroleerd.yml](../.github/workflows/xgboostV3_gecontroleerd.yml) | Handmatig/event | `xgboostV3_gecontroleerd.py` | 45 | workflow-xgboostv3_gecontroleerd-${{ github.ref }} | Niet toegewezen | repo-planning |

## Entry points uit andere checkouts

Deze paden worden uit een andere ref/repository uitgevoerd en zijn niet tegen de lokale hoofdbranch gevalideerd.

- `research_simulation.yml` / `simulate`: `research.py` uit `feature/quantify-principles`.

## Gelijke geplande startminuten

Dit zijn mogelijke pieken, geen bewijs dat runs werkelijk tegelijk zijn gestart. Looptijden kunnen ook tussen verschillende startminuten overlappen.

| Dagen | UTC | Workflows |
| --- | --- | --- |
| zo | 02:00 | run_02xgboostV2.yml, run__filtertickersmungerV2.yml |
| zo | 05:00 | a__parameter_model_production.yml, a_meta_labeling_classifier.yml, a_trade_combi.yml, analyse_parameter_ic.yml |
| ma, di, wo, do, vr, za | 05:00 | a__parameter_model_production.yml, a_trade_combi.yml |
| ma, di, wo, do, vr | 19:30 | run_00mail.yml, run_00mrEOD.yml |
| ma, di, wo, do, vr | 19:45 | run_00kr.yml, run_00ultmail.yml |
| ma, di, wo, do, vr | 19:50 | run_00ms.yml, run_01cointr.yml, run_01kasstr.yml, run_01marktsent.yml |
| ma, di, wo, do, vr | 19:55 | run_00db.yml, run_01hoogl.yml |
| ma, di, wo, do, vr | 20:00 | run_00cs.yml, run_00graham.yml |
| ma, di, wo, do, vr | 20:05 | run_00vcp.yml, run_01beurssign.yml |
| ma, di, wo, do, vr | 21:30 | run_generieke_fundamentals.yml, score_xgboost.yml |
| zo | 22:00 | a__meta_model_production.yml, run_forward_returns.yml |
| vr | 22:00 | analyse_correlatie_parameters.yml, run_00fisher.yml, run_forward_returns.yml |
| ma, di, wo, do | 22:00 | analyse_correlatie_parameters.yml, run_forward_returns.yml |
| vr | 23:00 | a__meta_model_production.yml, run_01hoogl_full.yml, run_02xgboostV3.yml, tradingagentsbridge.yml, weekly_run.yml |
| ma, di, wo, do | 23:00 | a__meta_model_production.yml, run_02xgboostV3.yml, tradingagentsbridge.yml |
| zo, za | 23:00 | run_02xgboostV3.yml, tradingagentsbridge.yml |

## Controlepunten

- Alle workflows binnen scope hebben een runbeveiliging, meerdere wachtplaatsen en begrensde jobduur; hun lokaal gecontroleerde entrypoints bestaan. Afwijkende checkouts staan afzonderlijk vermeld.
