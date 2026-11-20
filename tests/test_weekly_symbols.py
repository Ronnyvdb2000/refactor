import json
from pathlib import Path
import re

from weekly import shared as w
from weekly import symbols


def test_aliases_deduplicate_same_listing_but_keep_unknown_sources(monkeypatch, tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'tickers_051a.txt').write_text('CDPROJEKT.WA, CDR.WA, ONBEKEND.WA')
    (tmp_path / 'data' / 'tickers_054a.txt').write_text('KCR1V.HE, KCR.HE, 캐피탈.HE', encoding='utf-8')
    monkeypatch.setattr(w, 'ROOT', tmp_path)
    lists = w.laad_lijsten()
    assert lists['tickers_051a.txt'] == ['CDR.WA', 'ONBEKEND.WA']
    assert lists['tickers_054a.txt'] == ['KCR.HE', '캐피탈.HE']
    assert w.normaliseer_lijstticker('tickers_051x.txt', 'CDPROJEKT.WA') == 'CDR.WA'
    assert w.normaliseer_lijstticker('tickers_048a.txt', 'CDPROJEKT.WA') == 'CDPROJEKT.WA'
    assert not w.controleer_dekking('onbekend', ['ONBEKEND.WA'], [])


def test_symbol_registry_has_provenance_and_no_conflicts_or_alias_chains():
    with Path(symbols.__file__).with_suffix('.json').open(encoding='utf-8') as f:
        rows = json.load(f)
    assert len(rows) == len(symbols.GEVERIFIEERD)
    for row in rows:
        suffix = {'041': '.BR', '051': '.WA', '054': '.HE', '059': '.VI'}[row['lijstcode']]
        assert row['bron'].endswith(suffix) and row['doel'].endswith(suffix)
        assert row['bron'] != row['doel']
        assert (row['lijstcode'], row['doel']) not in symbols.GEVERIFIEERD
        assert re.fullmatch(r'[A-Z]{2}[A-Z0-9]{10}', row['isin'])
        assert row['bron_url'].startswith('https://')
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['gecontroleerd_op'])


def test_additional_market_codes_are_scoped_and_keep_unresolved_sources(monkeypatch, tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'tickers_041a.txt').write_text('CFE.BR, CELY.BR, NBB.BR, VPK.BR')
    (tmp_path / 'data' / 'tickers_054a.txt').write_text('EXL1V.HE, PONSSE.HE, TAALER.HE, OYJ.HE')
    (tmp_path / 'data' / 'tickers_059a.txt').write_text('BAWAG.VI, FREQ.VI, KRKG.LJ, 1TAT01DE')
    monkeypatch.setattr(w, 'ROOT', tmp_path)
    lists = w.laad_lijsten()
    assert set(lists['tickers_041a.txt']) == {'CFEB.BR', 'CYAD.BR', 'BNB.BR', 'VPK.BR'}
    assert set(lists['tickers_054a.txt']) == {'EXEL.HE', 'PON1V.HE', 'TAALA.HE', 'OYJ.HE'}
    assert set(lists['tickers_059a.txt']) == {'BG.VI', 'FQT.VI', 'KRKG.LJ', '1TAT01DE'}
    assert w.normaliseer_lijstticker('tickers_041x.txt', 'CFE.BR') == 'CFEB.BR'
    assert w.normaliseer_lijstticker('tickers_059x.txt', 'BAWAG.VI') == 'BG.VI'
    assert w.normaliseer_lijstticker('tickers_048a.txt', 'BAWAG.VI') == 'BAWAG.VI'


def test_existing_week_cache_survives_alias_change_without_wrong_ticker_reuse(monkeypatch, tmp_path):
    from datetime import date
    import pandas as pd
    from weekly.checkpoint import KoersCheckpoint

    monkeypatch.setenv('WEEKLY_CHECKPOINT_DIR', str(tmp_path))
    checkpoint = KoersCheckpoint(date(2026, 10, 26), date(2026, 11, 13))
    frame = pd.DataFrame({'Close': [100., 110.]},
                         index=pd.to_datetime(['2026-11-06', '2026-11-13']))
    checkpoint.bewaar({'OLD.VI': frame, 'GOOD.VI': frame})
    # Een nieuwe alias moet zijn eigen koersreeks ophalen; ongewijzigde
    # tickers uit hetzelfde weekbestand blijven wel herbruikbaar.
    assert set(checkpoint.laad(['NEW.VI', 'GOOD.VI'])) == {'GOOD.VI'}


def test_helsinki_source_matches_complete_official_snapshot():
    import pandas as pd
    from common.io import load_tickers_from_file

    root = Path(symbols.__file__).parents[1]
    official = pd.read_json(root / 'docs/controles/helsinki_universum_20261115.json', dtype=False)
    assert len(official) == 198 and official.ticker.nunique() == len(official)
    assert set(official.segment) == {'LARGE_CAP', 'MID_CAP', 'SMALL_CAP', 'FN'}
    assert official['isin'].str.fullmatch(r'[A-Z]{2}[A-Z0-9]{10}').all()
    assert official.bron_url.str.startswith('https://api.nasdaq.com/').all()
    assert set(load_tickers_from_file(root / 'data' / 'tickers_054a.txt')) == set(official.ticker)
    # Een officiële notering blijft meetellen, ook zonder Yahoo-koersdata.
    assert 'KESKOBN0126.HE' in official.ticker.tolist()
    assert not {'캐피탈.HE', 'OYJ.HE', 'ALMA2.HE'} & set(official.ticker)
