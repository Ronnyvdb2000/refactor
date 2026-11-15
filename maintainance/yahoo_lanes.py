"""Declared Yahoo job budgets for non-weekly GitHub Actions workflows.

Each lane is one native concurrency group shared across workflows/branches in
this repository. Jobs in other repositories and weekly jobs are outside scope.
"""
from pathlib import Path

LANES = {name: 'refactor-yahoo-' + name for name in ('daily', 'full', 'features', 'timed')}
JOB_LANES = {
    'a_trade_combi.yml': {'bot-combi-volatiel': 'daily'},
    'analyse_forward_correlaties.yml': {'forward-correlatie-analyse': 'features'},
    'run_analyse_selecties.yml': {'analyse': 'features'},
    'diag_koersen.yml': {'diag': 'features'},
    'backfill.yml': {'backfill': 'full'},
    'run_00cs.yml': {'bot-00cs': 'daily'},
    'run_00db.yml': {'bot-00db': 'daily'},
    'run_00dm.yml': {'bot-00dm': 'daily'},
    'run_00fisher.yml': {'bot-01fisher': 'daily'},
    'run_00graham.yml': {'bot-01graham': 'daily'},
    'run_00greenblatt.yml': {'bot-00greenblatt': 'daily'},
    'run_00kr.yml': {'bot-00kr': 'daily'},
    'run_00mail.yml': {'run_trading_bot': 'daily'},
    'run_00mrEOD.yml': {'bot-00mr-eod': 'daily'},
    'run_00mrORB.yml': {'bot-00mr-orb': 'timed'},
    'run_00ms.yml': {'bot-00ms': 'daily'},
    'run_00oshaughnessy.yml': {'bot-00oshaughnessy': 'daily'},
    'run_00supervar.yml': {'bot-00super-eu': 'timed', 'bot-00super-na': 'timed'},
    'run_00ultmail.yml': {'run_trading_bot': 'daily'},
    'run_00ultxxxV2.yml': {'run_global': 'daily'},
    'run_00vcp.yml': {'bot-00vcp': 'daily'},
    'run_01beurssign.yml': {'bot-01beurssig': 'daily'},
    'run_01beurssignfull.yml': {'bot-01beurssig-full': 'full'},
    'run_01cointr.yml': {'run-bot': 'cointegration-mode'},
    'run_01hoogl.yml': {'bot-01hoogl': 'daily'},
    'run_01hoogl_full.yml': {'bot-01hoogl-full': 'full'},
    'run_01kasstr.yml': {'bot-01kasstr': 'daily'},
    'run_01marktsent.yml': {'bot-01marktsent': 'daily'},
    'run_01repititief.yml': {'seizoensbot': 'daily'},
    'run_01volatieledipscan.yml': {'bot-01volhunter': 'full'},
    'run_02xgboost.yml': {'bot-01xgboost-live': 'daily', 'bot-01xgboost-train': 'full', 'bot-01xgboost-backtest': 'full'},
    'run__filtertickersmungerV2.yml': {'filter_tickers': 'full'},
    'run_generieke_technicals.yml': {'bouw-generieke-technicals': 'features'},
    'run_generieke_fundamentals.yml': {'bouw-generieke-fundamentals': 'features'},
    'run_forward_returns.yml': {'bouw-forward-returns': 'features'},
}


def group_for(lane):
    if lane == 'cointegration-mode':
        return "${{ github.event.inputs.mode == 'backtest' && 'refactor-yahoo-full' || 'refactor-yahoo-daily' }}"
    return LANES[lane]


def check_budget(root):
    import yaml
    errors = []
    for filename, jobs in JOB_LANES.items():
        path = Path(root) / '.github/workflows' / filename
        if not path.is_file():
            errors.append(f'{filename}: budget workflow missing')
            continue
        data = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job_id, lane in jobs.items():
            job = data.get('jobs', {}).get(job_id)
            if job is None:
                errors.append(f'{filename}/{job_id}: budget job missing')
                continue
            guard = job.get('concurrency', {})
            if guard.get('group') != group_for(lane) or guard.get('queue') != 'max' or guard.get('cancel-in-progress') != 'false':
                errors.append(f'{filename}/{job_id}: Yahoo lane budget differs from declaration')
            if data.get('concurrency', {}).get('group') == guard.get('group'):
                errors.append(f'{filename}/{job_id}: workflow and job share a locking group')
    return errors
