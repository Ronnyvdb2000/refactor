from pathlib import Path
import pandas as pd
from common.exports import write_csv


def test_csv_export_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv('CSV_EXPORT_ENABLED', raising=False)
    output = tmp_path / 'signals.csv'
    write_csv(pd.DataFrame({'ticker': ['A']}), output, index=False)
    assert not output.exists()


def test_csv_export_requires_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv('CSV_EXPORT_ENABLED', '1')
    output = tmp_path / 'signals.csv'
    write_csv(pd.DataFrame({'ticker': ['A']}), output, index=False)
    assert pd.read_csv(output).ticker.tolist() == ['A']


def test_portfolio_keeps_json_without_csv_projections(tmp_path, monkeypatch):
    from strats import bot_00xxxV2 as bot
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('CSV_EXPORT_ENABLED', raising=False)
    portfolio = bot.LivePortfolio(1000, load=False)
    portfolio.save_state('2026-11-19', {})
    assert Path('live_state.json').exists()
    assert list(tmp_path.glob('*.csv')) == []
    assert bot.LivePortfolio(0).cash == 1000


def test_parameter_outputs_keep_json_without_csv(tmp_path, monkeypatch):
    from meta_models import a_par_model as model
    monkeypatch.delenv('CSV_EXPORT_ENABLED', raising=False)
    monkeypatch.setattr(model, 'RESULTS_DIR', str(tmp_path))
    model.schrijf_rapporten(pd.DataFrame({'ticker':['A'], 'score':[.25]}),
                           pd.DataFrame({'parameter':['rsi14'], 'ic':[.1]}), '10d')
    assert pd.read_json(tmp_path / 'a_par_model_10d_picks.json').ticker.tolist() == ['A']
    assert (tmp_path / 'a_par_model_10d_weights.json').exists()
    assert list(tmp_path.glob('*.csv')) == []
