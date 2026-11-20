"""Date provenance, write guards and executed conflict SQL without live services."""
from contextlib import contextmanager
import re
import sqlite3
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import bouw_generieke_fundamentals as b
from common.fundamentals import KNOWN_FUNDAMENTALS_SQL
from meta_models import a_par_model as par
from analyses import analyse_parameter_ic as ic
import xgboostV4 as xgb


@pytest.fixture(autouse=True)
def day(monkeypatch):
    monkeypatch.setattr(b, 'observatiedatum', lambda: '2026-11-19')


def test_recent_selections_are_queue_only_not_destination_dates():
    connection = MagicMock()
    b.haal_openstaande_paren(connection)
    query, params = connection.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert params == dict(cutoff='2026-11-14', observatiedatum='2026-11-19')
    assert '%(observatiedatum)s::text AS datum' in query
    assert 'gf.datum = %(observatiedatum)s' in query
    assert 's.datum::date <= %(observatiedatum)s::date' in query


def test_cache_only_accepts_same_observation_day():
    connection = MagicMock()
    b.haal_recente_rijen(connection, ['A'])
    query, params = connection.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert params == dict(tickers=['A'], observatiedatum='2026-11-19')
    assert 'datum = %(observatiedatum)s' in query


@pytest.mark.parametrize('target', ['2026-11-18','2026-11-20',None])
def test_storage_refuses_backdated_future_and_unknown_dates(target):
    connection = MagicMock()
    with pytest.raises(ValueError, match='ophaaldatum'):
        b.upsert_rijen(connection, [dict(ticker='A', datum=target)])
    connection.cursor.assert_not_called()


def test_rerun_preserves_first_snapshot_using_actual_upsert_sql():
    database = sqlite3.connect(':memory:')
    database.execute('CREATE TABLE generieke_fundamentals (' + ','.join(
        name+' '+('REAL' if name=='market_cap' else 'TEXT') for name in b.KOLOMMEN)
        + ',bijgewerkt_op TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(ticker,datum))')
    class Cursor:
        @property
        def rowcount(self): return self.cursor.rowcount
        def execute(self, query, values):
            self.cursor = database.execute(re.sub(r'%\((\w+)\)s', r':\1', query), values)
    class Connection:
        @contextmanager
        def cursor(self): yield Cursor()
        def commit(self): database.commit()
    connection = Connection()
    first = dict(ticker='A', datum='2026-11-19', market_cap=100)
    assert b.upsert_rijen(connection, [first]) == 1
    stored = database.execute('SELECT market_cap,bijgewerkt_op FROM generieke_fundamentals').fetchone()
    assert b.upsert_rijen(connection, [dict(first,market_cap=999)]) == 0
    assert database.execute('SELECT market_cap,bijgewerkt_op FROM generieke_fundamentals').fetchone() == stored
    database.close()


def test_provider_result_never_fills_earlier_selection_dates(monkeypatch):
    monkeypatch.setattr(b, 'SUPABASE_DB_URL', 'offline')
    monkeypatch.setattr(b.psycopg2, 'connect', MagicMock())
    monkeypatch.setattr(b, 'haal_openstaande_paren', lambda _: [
        dict(ticker='A', datum='2026-11-17'), dict(ticker='A', datum='2026-11-18')])
    monkeypatch.setattr(b, 'haal_recente_rijen', lambda *_: {})
    monkeypatch.setattr(b, 'haal_fundamentals_op', lambda _: ({'market_cap':100},'ok'))
    monkeypatch.setattr(b.time, 'sleep', lambda *_: None)
    with patch.object(b, 'upsert_rijen', return_value=1) as store:
        b._run_build()
    store.assert_called_once()
    assert store.call_args.args[1] == [dict(ticker='A', datum='2026-11-19', market_cap=100)]


def test_all_fundamental_consumers_apply_daily_availability_guard():
    queries = [xgb.JOIN_QUERY]
    for module in [par, ic]:
        with patch.object(module.pd, 'read_sql', return_value=pd.DataFrame({'datum':[]})) as read:
            module.get_data(None)
        queries.append(read.call_args.args[0])
    assert all('WHERE '+KNOWN_FUNDAMENTALS_SQL in query for query in queries)
    assert all('LEFT JOIN' in query for query in queries)
