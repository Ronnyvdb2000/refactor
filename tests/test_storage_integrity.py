"""Storage integration tests in an isolated local PostgreSQL, never Supabase.

Install pgserver to run the PostgreSQL tests; no external DB credentials needed.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch
import math
import pandas as pd
import psycopg2
import pytest
import db_logger as db
import a_par_model as parameter
from combiners import a_trade_combi as combi
import bouw_forward_returns as forward


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    pgserver = pytest.importorskip('pgserver')
    try:
        handle = pgserver.get_server(tmp_path_factory.mktemp('storage-postgres'), cleanup_mode='delete')
    except PermissionError:
        pytest.skip('Local PostgreSQL sockets unavailable in this environment')
    yield handle
    handle.cleanup()


@pytest.fixture
def database(server, monkeypatch):
    dsn = server.get_uri()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS selecties,forward_returns')
            cur.execute('''CREATE TABLE selecties (
                id serial PRIMARY KEY,ticker text NOT NULL,datum text NOT NULL,
                strategie text NOT NULL,beurs text,koers real,parameters jsonb,
                created_at timestamp DEFAULT now(), score numeric,rank integer)''')
    monkeypatch.setattr(db, '_dsn_invalid', False)
    monkeypatch.setattr(db, '_get_connection', lambda: psycopg2.connect(dsn))
    return dsn


def row(**kwargs):
    return dict(ticker='A',datum='2026-11-13',strategie='test',beurs=None,
                koers=20.25,parameters={},**kwargs)


def count(dsn):
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM selecties')
        return cur.fetchone()[0]


def test_rerun_keeps_first_valid_snapshot(database):
    r=row()
    assert db.log_selectie(**r)
    r['koers']=99
    assert db.log_selectie(**r)
    assert count(database)==1
    with psycopg2.connect(database) as conn, conn.cursor() as cur:
        cur.execute('SELECT koers FROM selecties')
        assert cur.fetchone()[0]==20.25


def test_simultaneous_reruns_store_one_row(database):
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda _:db.log_selectie(**row()),range(24)))
    assert count(database)==1


def test_horizons_and_exchanges_are_distinct(database):
    for horizon,exchange in [('5d',None),('10d',None),('5d','Euronext')]:
        r=row();r['parameters']={'horizon':horizon};r['beurs']=exchange
        assert db.log_selectie(**r)
        assert db.log_selectie(**r)
    assert count(database)==3


def test_missing_price_repaired_without_new_row(database):
    r=row();r['koers']=None
    assert db.log_selectie(**r)
    assert db.log_selectie(**row())
    assert count(database)==1
    with psycopg2.connect(database) as conn, conn.cursor() as cur:
        cur.execute('SELECT koers FROM selecties')
        assert cur.fetchone()[0]==20.25


def test_bulk_row_failure_does_not_rollback_good_rows(database):
    bad=row();bad['parameters']={'rank':'not an integer'}
    good=row();good['ticker']='B';good['parameters']={'rank':2}
    assert db.log_selecties_bulk([bad,good])==1
    assert count(database)==1
    assert db.log_selecties_bulk([good])==0


def test_transient_connect_failure_can_recover(database):
    with patch.object(db,'_get_connection',side_effect=psycopg2.OperationalError('temporary')):
        assert not db.log_selectie(**row())
    assert db.log_selectie(**row())


def test_parameter_horizons_keep_valid_prices_and_skip_unknown(database):
    picks=pd.DataFrame([dict(ticker='A',score=.2,koers=20.25),dict(ticker='B',score=.1,koers=None)])
    with psycopg2.connect(database) as conn:
        assert parameter.schrijf_naar_selecties(conn,{'5d':picks,'10d':picks},pd.Timestamp('2026-11-13'))==2
        assert not conn.closed
        assert parameter.schrijf_naar_selecties(conn,{'5d':picks,'10d':picks},pd.Timestamp('2026-11-13'))==0
    assert count(database)==2
    assert callable(parameter.schrijf_model_metrics)


def test_parameter_same_day_price_never_selects_null_or_stale(database):
    # Execute the actual LATERAL subquery from get_data against a small fixture.
    r=row();r.update(koers=None);assert db.log_selectie(**r)
    r=row();r.update(strategie='valid');assert db.log_selectie(**r)
    r=row();r.update(ticker='B',datum='2026-11-12');assert db.log_selectie(**r)
    captured=[]
    with patch.object(parameter.pd,'read_sql',side_effect=lambda q,c:(captured.append(q) or pd.DataFrame({'datum':[]}))):
        parameter.get_data(None)
    lateral=captured[0].split('LEFT JOIN LATERAL (')[1].split(') sel ON TRUE')[0]
    with psycopg2.connect(database) as conn,conn.cursor() as cur:
        cur.execute("SELECT t.ticker,sel.koers FROM (VALUES ('A','2026-11-13'),('B','2026-11-13')) t(ticker,datum) LEFT JOIN LATERAL ("+lateral+") sel ON TRUE")
        assert cur.fetchall()==[('A',20.25),('B',None)]


def test_forward_upsert_preserves_labels_and_entry_on_conflict(database,monkeypatch):
    monkeypatch.setattr(forward,'HORIZONS',[5,10])
    with psycopg2.connect(database) as conn:
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE forward_returns (
                ticker text,datum text,strategie text,beurs text,entry_koers float8,
                fwd_close_5d float8,fwd_ret_5d float8,fwd_close_10d float8,fwd_ret_10d float8,
                bijgewerkt_op timestamp,PRIMARY KEY(ticker,datum,strategie))''')
        r=dict(ticker='A',datum='2026-11-13',strategie='test',beurs=None,entry_koers=100,
               fwd_close_5d=110,fwd_ret_5d=10,fwd_close_10d=None,fwd_ret_10d=None)
        assert forward.upsert_labels(conn,[r])==1
        # Same basis: a new longer horizon may mature without erasing 5d.
        next_row=dict(r,fwd_close_5d=None,fwd_ret_5d=None,fwd_close_10d=120,fwd_ret_10d=20)
        assert forward.upsert_labels(conn,[next_row])==1
        # A different source entry may not silently change the denominator.
        assert forward.upsert_labels(conn,[dict(next_row,entry_koers=200)])==0
        with conn.cursor() as cur:
            cur.execute('SELECT entry_koers,fwd_ret_5d,fwd_ret_10d FROM forward_returns')
            assert cur.fetchone()==(100,10,20)


def test_quote_for_combi_comes_from_completed_session_without_rounding():
    dates=pd.bdate_range(end='2026-11-13',periods=20)
    raw=pd.DataFrame({'Ticker':'A','Date':dates,'Open':20.,'High':22.,'Low':18.,'Close':20.123456,'Volume':100.})
    future=raw.tail(1).copy();future['Date']=pd.Timestamp('2026-11-14');future['Close']=999.
    raw=pd.concat([raw,future],ignore_index=True)
    quotes={}
    with patch.object(combi,'today_str',return_value='2026-11-14'), \
        patch.object(combi,'download_universe',return_value=raw):
        assert combi.compute_atr_pct({'market':['A']},['A'],quotes)['A']>0
        assert quotes=={'A':(20.123456,'2026-11-13')}


def test_combi_passes_real_quote_and_date_to_storage():
    def atr(exchanges,tickers,quotes,**kwargs):
        quotes['A']=(20.123456,'2026-11-13')
        return {'A':5}
    signals={'market':{'A'}}
    with patch.dict("os.environ", {"SUPABASE_DB_URL":"offline"}), patch.multiple(combi, today_str=lambda:'2026-11-14',herbereken_gewichten=lambda:{'vcp':1},
        bouw_exchange_tickers=lambda:({'market':['A']},['A']), warm_up_yfinance=lambda:True,download_universe=lambda *a:pd.DataFrame(),
        compute_atr_pct=atr,selecties_repititief=lambda *a:signals,selecties_vcp=lambda *a,**kw:signals,
        selecties_kasstr=lambda *a:signals,selecties_fisher=lambda *a:signals,selecties_hoogl=lambda *a:signals,
        send_email=lambda **k:True,send_telegram=lambda *a,**k:True), \
        patch.object(combi.db_logger,'log_selectie',return_value=True) as log:
        combi.run_live_engine()
        assert log.call_args.kwargs['koers']==20.123456
        assert log.call_args.kwargs['datum']=='2026-11-13'


def test_lock_precedes_lookup_and_duplicate_does_not_insert():
    from unittest.mock import MagicMock
    cur=MagicMock();cur.fetchone.return_value=(7,20.25)
    r=row();r['parameters']={'horizon':'10d'}
    assert not db._write_selectie(cur,r)
    calls=cur.execute.call_args_list
    assert 'pg_advisory_xact_lock' in calls[0].args[0]
    assert 'SELECT id, koers' in calls[1].args[0]
    assert calls[1].args[1]==('A','2026-11-13','test',None,'10d',None)
    assert len(calls)==2


def test_missing_price_repair_targets_existing_id():
    from unittest.mock import MagicMock
    cur=MagicMock();cur.fetchone.return_value=(7,None)
    assert db._write_selectie(cur,row())
    assert cur.execute.call_args.args==('UPDATE selecties SET koers = %s WHERE id = %s',(20.25,7))


def test_bulk_savepoint_recovery_and_commit_count():
    from unittest.mock import MagicMock
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    with patch.object(db,'_write_selectie',side_effect=[ValueError('bad row'),True,False]):
        assert db.log_selecties_bulk([row(),row(),row()],connection=conn)==1
    commands=[call.args[0] for call in cur.execute.call_args_list]
    assert commands.count('SAVEPOINT selectie_row')==3
    assert 'ROLLBACK TO SAVEPOINT selectie_row' in commands
    conn.commit.assert_called_once();conn.close.assert_not_called()


def test_bulk_failed_commit_reports_zero_and_rolls_back():
    from unittest.mock import MagicMock
    conn=MagicMock();conn.commit.side_effect=psycopg2.OperationalError('commit failed')
    with patch.object(db,'_write_selectie',return_value=True):
        assert db.log_selecties_bulk([row()],connection=conn)==0
    conn.rollback.assert_called_once()


def test_datetime_key_is_calendar_date_and_parameters_sanitized():
    r=row();r['datum']=datetime(2026,11,13,12,30);r['parameters']={'score':float('nan')}
    result=db._prepare_row(r)
    assert result['datum']=='2026-11-13'
    assert result['parameters']['score'] is None


def test_postgresql_statements_and_forward_guard_without_network_sockets(tmp_path,monkeypatch):
    """Run actual generated SQL in PostgreSQL's standalone backend."""
    import subprocess
    from unittest.mock import MagicMock
    from psycopg2 import sql,extensions
    pgserver=pytest.importorskip('pgserver')
    pgserver.initdb(['-A','trust','--no-locale'],pgdata=tmp_path)
    def render(statement):
        if isinstance(statement,str):return statement
        if isinstance(statement,sql.SQL):return statement.string
        if isinstance(statement,sql.Identifier):return '.'.join('"'+x.replace('"','""')+'"' for x in statement.strings)
        if isinstance(statement,sql.Placeholder):return '%s'
        if isinstance(statement,sql.Composed):return ''.join(render(x) for x in statement.seq)
        raise TypeError(statement)
    cur=MagicMock();cur.fetchone.return_value=None
    db._write_selectie(cur,row())
    statements=['CREATE TABLE selecties (id serial PRIMARY KEY,ticker text,datum text,strategie text,beurs text,koers real,parameters jsonb,created_at timestamp DEFAULT now());','BEGIN;']
    for call in cur.execute.call_args_list:
        statement,values=call.args
        statements.append(render(statement)%tuple(extensions.adapt(v).getquoted().decode() for v in values)+';')
    statements+=['COMMIT;',"DO $$ BEGIN IF (SELECT count(*) FROM selecties) <> 1 THEN RAISE EXCEPTION 'bad selection count'; END IF; END $$;"]
    monkeypatch.setattr(forward,'HORIZONS',[5,10])
    statements.append('CREATE TABLE forward_returns(ticker text,datum text,strategie text,beurs text,entry_koers float8,fwd_close_5d float8,fwd_ret_5d float8,fwd_close_10d float8,fwd_ret_10d float8,bijgewerkt_op timestamp,PRIMARY KEY(ticker,datum,strategie));')
    r=dict(ticker='A',datum='2026-11-13',strategie='test',beurs=None,entry_koers=100,fwd_close_5d=110,fwd_ret_5d=10,fwd_close_10d=None,fwd_ret_10d=None)
    for update in [r,dict(r,fwd_close_5d=None,fwd_ret_5d=None,fwd_close_10d=120,fwd_ret_10d=20),dict(r,entry_koers=200)]:
        statements.append(forward.bouw_upsert_query()%{k:extensions.adapt(v).getquoted().decode() for k,v in update.items()})
    statements.append("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM forward_returns WHERE entry_koers=100 AND fwd_ret_5d=10 AND fwd_ret_10d=20) THEN RAISE EXCEPTION 'changed entry or erased labels'; END IF; END $$;")
    binary=pgserver.postgres.__globals__['POSTGRES_BIN_PATH']/'postgres'
    result=subprocess.run([str(binary),'--single','-D',str(tmp_path),'postgres'],
        input='\n'.join(' '.join(s.splitlines()) for s in statements)+'\n',text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'ERROR:' not in result.stderr,result.stderr
    assert 'id = "1"' not in result.stdout  # lookup before insert was empty
