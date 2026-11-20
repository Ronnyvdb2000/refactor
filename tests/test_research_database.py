from datetime import date
import pytest
from research.database import basket_from_rows, MARKET, STRATEGY

TODAY = date(2026, 11, 20)


def row(id=1, **kw):
    return dict(id=id, ticker='A', beurs=MARKET, strategie=STRATEGY,
                datum='2026-11-19', koers=100, parameters={
                    'signal_date': '2026-11-19', 'price_basis': 'yahoo_auto_adjusted_close',
                    'atr_pct': 4, 'gewogen_score': 1}, **kw)


def test_latest_date_and_duplicate_tickers():
    older = row(3)
    older['datum'] = '2026-11-18'
    newer = row(2)
    newer['koers'] = 101
    result = basket_from_rows([row(), older, newer], today=TODAY)
    assert len(result['signals']) == 1
    assert result['signals'][0]['reference_price'] == 101
    assert result['currency'] == 'USD'


@pytest.mark.parametrize('change', ['old', 'future', 'nan', 'currency', 'basis', 'date', 'market'])
def test_invalid_data_stops_whole_basket(change):
    r = row()
    if change == 'old': r['datum'] = '2026-10-01'
    if change == 'future': r['datum'] = '2026-11-21'
    if change == 'nan': r['koers'] = float('nan')
    if change == 'currency': r['parameters']['currency'] = 'EUR'
    if change == 'basis': r['parameters']['price_basis'] = 'unknown'
    if change == 'date': r['parameters']['signal_date'] = '2026-11-18'
    if change == 'market': r['beurs'] = 'other'
    with pytest.raises(ValueError): basket_from_rows([r], today=TODAY)


def test_empty_source_does_not_simulate_success():
    with pytest.raises(ValueError): basket_from_rows([], today=TODAY)


def test_connection_is_read_only_and_closed(monkeypatch):
    import sys
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from research.database import load_basket
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [row()]
    connect = MagicMock(return_value=connection)
    monkeypatch.setitem(sys.modules, 'psycopg2', SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, 'psycopg2.extras', SimpleNamespace(RealDictCursor=object))
    monkeypatch.setenv('SUPABASE_DB_URL', 'private-url')
    assert load_basket(today=TODAY)['signals'][0]['ticker'] == 'A'
    connection.set_session.assert_called_once_with(readonly=True, autocommit=False)
    connection.close.assert_called_once()
    assert 'statement_timeout' in cursor.execute.call_args_list[0].args[0]
    assert cursor.execute.call_args_list[1].args[1][0] == MARKET


def test_database_error_does_not_expose_url(monkeypatch):
    import sys
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from research.database import load_basket
    monkeypatch.setitem(sys.modules, 'psycopg2', SimpleNamespace(connect=MagicMock(side_effect=Exception('private-url'))))
    monkeypatch.setitem(sys.modules, 'psycopg2.extras', SimpleNamespace(RealDictCursor=object))
    monkeypatch.setenv('SUPABASE_DB_URL', 'private-url')
    with pytest.raises(RuntimeError) as error:
        load_basket(today=TODAY)
    assert 'private-url' not in str(error.value)
