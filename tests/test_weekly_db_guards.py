from datetime import date
from decimal import Decimal
from pathlib import Path
import subprocess
import pytest
from psycopg2.extensions import adapt
from weekly import weekly_db_opvolg as db


class Capture:
    def __init__(self, fail=False):
        self.calls=[]
        self.closed=False
        self.fail=fail
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def cursor(self):return self
    def execute(self,query,params):
        self.calls.append((query,params))
        if self.fail:raise RuntimeError('database failure')
    def fetchall(self):return []
    def close(self):self.closed=True


def rendered(query,params):
    return query % tuple(adapt(v).getquoted().decode() for v in params)


def test_database_failures_propagate(monkeypatch):
    conn=Capture(fail=True)
    monkeypatch.setattr(db,'_get_connection',lambda:conn)
    with pytest.raises(RuntimeError,match='database failure'):
        db.log_weekly_toppers([dict(week_startdatum='2026-11-16',lijst='A',ticker='ABC',beurs='X',type='stijger',week_perf=10)])
    assert conn.closed


def test_nonfinite_decimal_sanitized():
    assert db._sanitize(Decimal('NaN')) is None
    assert db._sanitize(float('inf')) is None
    assert db._sanitize('X')=='X'


def test_upserts_and_asof_query_in_postgres(monkeypatch,tmp_path):
    pgserver=pytest.importorskip('pgserver')
    cluster=tmp_path/'postgres'
    pgserver.initdb(['-A','trust','--no-locale'],pgdata=cluster)
    conn=Capture()
    monkeypatch.setattr(db,'_get_connection',lambda:conn)
    topper=dict(week_startdatum='2026-11-16',lijst='A',ticker='ABC',beurs='X',type='stijger',rang=1,week_perf=10)
    db.log_weekly_toppers([topper])
    db.log_weekly_toppers([{**topper,'type':'daler','week_perf':-20}])
    param=dict(week_startdatum='2026-11-16',ticker='ABC',beurs='X',datum='2026-11-16',dag_index=1,close=100)
    db.log_weekly_topper_parameters([param])
    db.log_weekly_topper_parameters([{**param,'close':None,'rsi':50}])
    db.haal_laatste_rank_scores(['ABC'],date(2026,11,16),'X')
    queries=[rendered(q,p) for q,p in conn.calls]
    schema=[
        'CREATE TABLE weekly_toppers (week_startdatum date,lijst text,ticker text,beurs text,type text,rang smallint,week_perf numeric, UNIQUE(ticker,beurs,week_startdatum,type))',
        'CREATE TABLE weekly_topper_parameters (week_startdatum date,ticker text,beurs text,datum date,dag_index smallint,'+','.join(k+' numeric' for k in db._PARAM_KOLOMMEN)+', UNIQUE(ticker,beurs,datum))',
        'CREATE TABLE selecties (id integer,ticker text,beurs text,datum text,created_at timestamptz,'+','.join(k+' numeric' for k in db._RANK_KOLOMMEN)+')',
        "INSERT INTO selecties(id,ticker,beurs,datum,created_at,combined_rank) VALUES (1,'ABC','X','2026-11-10','2026-11-10',1),(2,'ABC','X','2026-11-20','2026-11-20',99),(3,'ABC','X','2026-11-15','2026-11-21',88),(4,'ABC','Y','2026-11-16','2026-11-16',77)",
        "INSERT INTO selecties(id,ticker,beurs,datum,created_at,roc_rank) VALUES (5,'ABC','X','2026-11-16','2026-11-16',5)"]
    sql=schema+queries[:-1]+['CREATE TEMP TABLE ranks AS '+queries[-1]]+[
        "DO $$ BEGIN IF (SELECT count(*) FROM weekly_toppers) <> 1 OR (SELECT week_perf FROM weekly_toppers) <> 10 THEN RAISE EXCEPTION 'duplicate or changed snapshot'; END IF; IF (SELECT close FROM weekly_topper_parameters) <> 100 OR (SELECT rsi FROM weekly_topper_parameters) <> 50 THEN RAISE EXCEPTION 'lost parameters'; END IF; IF (SELECT count(*) FROM ranks) <> 2 OR NOT EXISTS (SELECT 1 FROM ranks WHERE key='combined_rank' AND numeric=1) OR NOT EXISTS (SELECT 1 FROM ranks WHERE key='roc_rank' AND numeric=5) THEN RAISE EXCEPTION 'rank leakage'; END IF; END $$"]
    binary=pgserver.postgres.__globals__['POSTGRES_BIN_PATH']/'postgres'
    result=subprocess.run([str(binary),'--single','-D',str(cluster),'postgres'],
                          input='\n'.join(' '.join(s.split())+';' for s in sql)+'\n',
                          text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'ERROR:' not in result.stderr,result.stderr
