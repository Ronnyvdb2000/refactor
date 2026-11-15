from datetime import date

import pandas as pd
import json
import pytest

from weekly import shared as w
from weekly.coverage import deelmarkt, marktdekking, voldoende


def test_market_breakdown_keeps_unknown_sources_in_total(monkeypatch, tmp_path):
    monkeypatch.setattr(w, 'ROOT', tmp_path)
    naam = 'tickers_059a.txt'
    tickers = ['BG.VI', 'OBS.VI', 'KRKG.LJ', '1TAT01DE', 'ONBEKEND']
    w.schrijf_dekking('a', {naam: tickers}, {naam: [{'ticker': 'BG.VI'}, {'ticker': 'OBS.VI'}]})
    rows = pd.read_csv(tmp_path / 'weekly_dekking_a.csv')
    summary = pd.read_csv(tmp_path / 'weekly_marktdekking_a.csv').set_index('deelmarkt')
    assert len(rows) == summary.aantal.sum() == 5
    assert summary.loc['Wenen', 'bruikbaar'] == 2
    assert summary.loc['Wenen', 'status'] == 'voldoende'
    assert summary.loc['Ljubljana', 'status'] == 'onvoldoende'
    assert summary.loc['Bratislava (broncode)', 'aantal'] == 1
    assert summary.loc['Onbekende markt', 'aantal'] == 1
    assert not voldoende(len(tickers), 2)
    assert deelmarkt('tickers_048a.txt', 'KRKG.LJ') == 'Gehele lijst'


def test_aborted_market_is_unknown_instead_of_zero_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(w, 'ROOT', tmp_path)
    naam = 'tickers_059a.txt'
    cache = w.WeekKoersen(date(2026, 11, 9), date(2026, 11, 13))
    cache.frames['BG.VI'] = pd.DataFrame()
    cache.aangevraagd.update(['BG.VI', 'OBS.VI'])
    w.schrijf_dekking('a', {naam: ['BG.VI', 'OBS.VI', 'KRKG.LJ']}, {}, koersen=cache)
    summary = pd.read_csv(tmp_path / 'weekly_marktdekking_a.csv').set_index('deelmarkt')
    assert summary.loc['Wenen', 'aantal'] == 2
    assert summary.loc['Wenen', 'onbekend'] == 1
    assert summary.loc['Ljubljana', 'onbekend'] == 1
    assert summary.dekking.isna().all()
    assert set(summary.status) == {'niet_volledig_onderzocht'}


@pytest.mark.parametrize("suffix", ["a", "x"])
def test_partial_report_publishes_only_sufficient_markets(monkeypatch, tmp_path, suffix):
    naam = f'tickers_059{suffix}.txt'
    lijsten = {'tickers_01.txt': ['GOOD'], naam: ['BG.VI', 'KRKG.LJ', '1TAT01DE']}
    entry = lambda t: dict(ticker=t, perf=10., basisdatum='2026-11-06', einddatum='2026-11-13')
    monkeypatch.setattr(w, 'ROOT', tmp_path)
    monkeypatch.setattr(w, 'weekgrenzen', lambda: (date(2026, 11, 9), date(2026, 11, 13)))
    monkeypatch.setattr(w, 'laad_lijsten', lambda *_: lijsten)
    monkeypatch.setattr(w.WeekKoersen, 'laad', lambda *_: None)
    monkeypatch.setattr(w.WeekKoersen, 'performance', lambda self, ts: [entry(t) for t in ts if t in {'GOOD', 'BG.VI'}])
    messages, emails = [], []
    monkeypatch.setattr(w, 'meld', messages.append)
    monkeypatch.setattr(w, 'send_email', lambda *args: emails.append(args))
    snapshot = tmp_path / ('laatste_toppers_x.json' if suffix == 'x' else 'laatste_toppers.json')
    snapshot.write_text('oude snapshot')
    w.maak_rapport(suffix)
    rows = json.loads(snapshot.read_text())
    assert {r['ticker'] for r in rows} == {'GOOD', 'BG.VI'}
    vienna = next(r for r in rows if r['ticker'] == 'BG.VI')
    assert vienna['beurs'] == '059 Wenen'
    assert vienna['aantal_universum'] == vienna['aantal_bruikbaar'] == 1
    assert {r['rapport_status'] for r in rows} == {'gedeeltelijk'}
    assert len(messages) == len(emails) == 1
    assert '2/4 beurs-/themagroepen voldoende' in messages[0]
    assert 'GEDEELTELIJK' in messages[0] and 'geen nieuwe toppers' not in messages[0]
    assert 'Wenen: 1/1' in messages[0] and 'Ljubljana: 0/1' in messages[0]
    assert 'Bratislava (broncode): 0/1' in messages[0]
    assert 'GOOD: +10.00%' in messages[0]
    assert 'BG.VI: +10.00%' in messages[0]
    assert 'GOOD: +10.00%' in (tmp_path / f'weekly_rapport_{suffix}.txt').read_text()
    assert (tmp_path / f'weekly_marktdekking_{suffix}.csv').exists()


def test_market_summary_does_not_treat_unrequested_data_as_failure():
    result = marktdekking([dict(lijst='x', deelmarkt='markt', bruikbaar=True),
                          dict(lijst='x', deelmarkt='markt', bruikbaar=None)])
    assert result[0]['dekking'] is None
    assert result[0]['onbekend'] == 1


def test_failed_market_is_not_ranked_even_with_extreme_winner():
    lijsten = {'tickers_059a.txt': ['A.VI', 'B.VI', 'C.VI', 'D.VI', 'KRKG.LJ']}
    groepen = w.publicatiegroepen(lijsten, {'tickers_059a.txt': [
        dict(ticker='A.VI', perf=1000), dict(ticker='KRKG.LJ', perf=1)]})
    assert [(g['label'], g['voldoende']) for g in groepen] == [
        ('059 Wenen', False), ('059 Ljubljana', True)]


def test_report_telegram_parts_include_entire_text_and_log_delivery(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(w, 'send_telegram', lambda text, **kw: sent.append((text, kw)) or True)
    tekst = 'GEDEELTELIJK WEEKRAPPORT\n' + 'Stijgers en dalers\n' * 600
    w.meld(tekst)
    assert ''.join(text for text, _ in sent) == tekst
    assert len(sent) > 1
    assert all(len(text) <= 3900 and kw['parse_mode'] is None for text, kw in sent)
    assert f'Telegram deel {len(sent)}/{len(sent)} geaccepteerd' in capsys.readouterr().out


def test_telegram_failure_is_visible_as_workflow_warning(monkeypatch, capsys):
    monkeypatch.setattr(w, 'send_telegram', lambda *args, **kwargs: False)
    w.meld('Weekrapport')
    assert '::warning::Telegram deel 1/1 niet verzonden' in capsys.readouterr().out
