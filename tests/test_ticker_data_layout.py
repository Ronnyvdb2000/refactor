"""Ticker paths and filter publication, without scans or external services."""
from pathlib import Path
import subprocess

import pytest
import yaml
from common.io import bouw_bestandslijst, label_voor, load_tickers_from_file

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('suffix', ['a', 'x'])
def test_shared_universes_resolve_actual_lists_and_preserve_market_names(suffix):
    paths = bouw_bestandslijst(suffix)
    assert len(paths) == 19
    for path in paths:
        assert Path(path).parent == Path('data')
        assert (ROOT / path).is_file()
        assert label_voor(path) == label_voor(Path(path).name)
    assert load_tickers_from_file(ROOT / 'data/tickers_048x.txt')
    assert not list(ROOT.glob('tickers_*.txt'))


def test_filter_reads_and_writes_all_variants_in_data(tmp_path, monkeypatch):
    from scripts import bot_041mV2 as bot
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    source = Path(bot.pad_bron('048'))
    source.write_text('AAPL, MSFT')
    bot.sla_delisted_op('048', {'DEAD': '2026-11-19'})
    assert bot.laad_delisted('048') == {'DEAD': '2026-11-19'}
    master = {ticker: {'ticker': ticker, 'status': status} for ticker, status in
              [('AAPL', 'actief'), ('MSFT', 'zwakker'), ('DEAD', 'actief')]}
    bot.sla_master_op('048', master, 'Nasdaq')
    assert set(bot.laad_master('048')) == set(master)
    assert bot.sla_export_op('048', master, {'DEAD'}) == ['AAPL', 'MSFT']
    assert load_tickers_from_file(bot.pad_export('048')) == ['AAPL', 'MSFT']
    assert source.read_text() == 'AAPL, MSFT'
    assert not list(tmp_path.glob('tickers_*.txt'))
    assert len(list((tmp_path / 'data').glob('tickers_*.txt'))) == 4


def test_custom_consumers_use_data_and_us_filter_keeps_market_identity():
    from scripts import bouw_generieke_technicals as technical
    from strats import bot_00supervar as superbot, bot_01volhunter as vol, bot_01beurssign as signal, bot_00mr as mr
    assert technical.bouw_bestandslijst() == bouw_bestandslijst()
    assert superbot.pad_export('048') == 'data/tickers_048x.txt'
    assert vol.bouw_bestandslijst_a() == bouw_bestandslijst('a')
    assert all(Path(path).parent == Path('data') for path in mr.ORB_EXCHANGES.values())
    assert signal.is_us_beurs('data/tickers_048x.txt')
    assert not signal.is_us_beurs('data/tickers_045x.txt')


def test_ticker_filter_workflow_stages_only_generated_lists_under_data(tmp_path):
    path = ROOT / '.github/workflows/run__filtertickersmungerV2.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['filter_tickers']['steps']
    command = next(step['run'] for step in steps if step.get('name') == 'Opslaan resultaten')
    add = next(line.strip() for line in command.splitlines() if line.strip().startswith('git add '))
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'data').mkdir()
    for suffix in 'amxd':
        (tmp_path / f'data/tickers_048{suffix}.txt').write_text('A')
    (tmp_path / 'unrelated.txt').write_text('do not stage')
    subprocess.run(['bash', '-e', '-c', add], cwd=tmp_path, check=True)
    staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=tmp_path, text=True).splitlines()
    assert sorted(staged) == sorted(f'data/tickers_048{suffix}.txt' for suffix in 'amxd')
