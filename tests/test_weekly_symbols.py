import csv
from pathlib import Path
import re

from weekly import shared as w
from weekly import symbols


def test_aliases_deduplicate_same_listing_but_keep_unknown_sources(monkeypatch, tmp_path):
    (tmp_path / 'tickers_051a.txt').write_text('CDPROJEKT.WA, CDR.WA, ONBEKEND.WA')
    (tmp_path / 'tickers_054a.txt').write_text('KCR1V.HE, KCR.HE, 캐피탈.HE', encoding='utf-8')
    monkeypatch.setattr(w, 'ROOT', tmp_path)
    lists = w.laad_lijsten()
    assert lists['tickers_051a.txt'] == ['CDR.WA', 'ONBEKEND.WA']
    assert lists['tickers_054a.txt'] == ['KCR.HE', '캐피탈.HE']
    assert w.normaliseer_lijstticker('tickers_051x.txt', 'CDPROJEKT.WA') == 'CDR.WA'
    assert w.normaliseer_lijstticker('tickers_048a.txt', 'CDPROJEKT.WA') == 'CDPROJEKT.WA'
    assert not w.controleer_dekking('onbekend', ['ONBEKEND.WA'], [])


def test_symbol_registry_has_provenance_and_no_conflicts_or_alias_chains():
    with Path(symbols.__file__).with_suffix('.csv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(symbols.GEVERIFIEERD)
    for row in rows:
        suffix = {'051': '.WA', '054': '.HE'}[row['lijstcode']]
        assert row['bron'].endswith(suffix) and row['doel'].endswith(suffix)
        assert row['bron'] != row['doel']
        assert (row['lijstcode'], row['doel']) not in symbols.GEVERIFIEERD
        assert re.fullmatch(r'[A-Z]{2}[A-Z0-9]{10}', row['isin'])
        assert row['bron_url'].startswith('https://')
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['gecontroleerd_op'])
