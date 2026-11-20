from datetime import date
from pathlib import Path
import json
import pandas as pd
import pytest
from weekly import shared as w
from weekly import selection_storage as store
from weekly import weekly_db_opvolg as db
from test_weekly_db_guards import Capture, rendered

MON, FRI = date(2026,11,9), date(2026,11,13)


@pytest.mark.parametrize('suffix', ['a','x'])
def test_reports_store_only_own_top_five_strictly_above_ten(monkeypatch,tmp_path,suffix):
    monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w,'weekgrenzen',lambda:(MON,FRI))
    monkeypatch.setattr(w,'laad_lijsten',lambda *_:{'tickers_01.txt':list('ABCDEFGH')})
    monkeypatch.setattr(w.WeekKoersen,'laad',lambda *_:None)
    monkeypatch.setattr(w.WeekKoersen,'performance',lambda *_:[dict(ticker=t,perf=p,
        basisdatum='2026-11-06',einddatum='2026-11-13') for t,p in zip('ABCDEFGH',[30,25,20,15,10.004,10,0,-20])])
    calls=[]
    monkeypatch.setattr(w,'meld',lambda *_:calls.append('telegram'))
    monkeypatch.setattr(w,'send_email',lambda *_:None)
    monkeypatch.setattr(store,'bewaar_selecties',lambda rows,*_:calls.append(rows))
    w.maak_rapport(suffix)
    assert calls[0]=='telegram'
    assert [r['ticker'] for r in calls[1]]==list('ABCDE')
    assert {r['bronselectie'] for r in calls[1]}=={suffix}
    assert calls[1][-1]['week_perf']==10.004  # Filter vóór afronding.
    assert calls[1][-1]['rang']==5


def test_empty_selection_does_not_connect_or_download(monkeypatch):
    monkeypatch.setattr(db,'log_report_toppers',lambda *_:pytest.fail('DB zonder winnaar'))
    monkeypatch.setattr(w.WeekKoersen,'laad',lambda *_:pytest.fail('Download zonder winnaar'))
    store.bewaar_selecties([],MON,FRI)


def test_pre_week_snapshot_never_sees_winning_week(monkeypatch):
    idx=pd.bdate_range(end='2026-11-13',periods=260)
    close=pd.Series(range(100,360),index=idx,dtype=float)
    hist=pd.DataFrame({'Close':close,'Open':close,'High':close+1,'Low':close-1,'Volume':100})
    hist.loc['2026-11-09':,'Close']=9999 # Geen lekkage naar de snapshot.
    captured=[]
    monkeypatch.setattr(w.WeekKoersen,'laad',lambda self,ts:self.frames.update({'A':hist}))
    monkeypatch.setattr(db,'log_report_toppers',lambda rows:len(rows))
    monkeypatch.setattr(db,'haal_laatste_rank_scores',lambda ts,d,b:captured.append((d,b)) or {'A':{'combined_rank':4}})
    monkeypatch.setattr(db,'log_weekly_topper_parameters',lambda rows:captured.extend(rows))
    entry=dict(ticker='A',beurs='X',type='stijger',week_perf=20,lijst='x',bronselectie='x')
    store.bewaar_selecties([entry],MON,FRI)
    assert captured[0]==(date(2026,11,6),'X')
    row=captured[1]
    assert row['datum']==date(2026,11,6) and row['week_startdatum']==MON
    assert row['dag_index']==0 and row['close']==354 and row['combined_rank']==4
    assert row['dist_sma200_pct'] is not None


def test_missing_history_keeps_selection_with_warning(monkeypatch,capsys):
    selected=[]
    monkeypatch.setattr(db,'log_report_toppers',lambda rows:selected.extend(rows))
    monkeypatch.setattr(w.WeekKoersen,'laad',lambda *_:None)
    monkeypatch.setattr(db,'log_weekly_topper_parameters',lambda rows:None)
    missing = store.bewaar_selecties([dict(ticker='A',beurs='X',type='stijger',week_perf=20)],MON,FRI)
    assert selected[0]['ticker']=='A'
    assert missing==['A']
    assert '::warning::Historische parameters ontbreken voor: A' in capsys.readouterr().out


def test_report_membership_and_parameters_sql_in_postgres(monkeypatch,tmp_path):
    pgserver=pytest.importorskip('pgserver')
    import subprocess
    cluster=tmp_path/'postgres'
    pgserver.initdb(['-A','trust','--no-locale'],pgdata=cluster)
    conn=Capture()
    monkeypatch.setattr(db,'_get_connection',lambda:conn)
    entry=dict(ticker='A',beurs='X',week_startdatum=MON,type='stijger',week_perf=12,lijst='a',rang=1,bronselectie='a')
    for source in ['a','x','a','x']:
        db.log_report_toppers([dict(entry,bronselectie=source,week_perf=12 if source=='a' else 99)])
    schema=["CREATE TABLE weekly_toppers(id serial primary key,created_at timestamptz default now(),ticker text,beurs text,week_startdatum date,type text,week_perf numeric,lijst text,rang int,bronselectie text,unique(ticker,beurs,week_startdatum,type))",
            "CREATE UNIQUE INDEX report_key ON weekly_toppers(ticker,beurs,week_startdatum) WHERE bronselectie IS NOT NULL"]
    queries=[rendered(q,p) for q,p in conn.calls]
    assertions="DO $$ BEGIN IF (SELECT count(*) FROM weekly_toppers)<>1 OR (SELECT bronselectie FROM weekly_toppers)<>'a,x' OR (SELECT week_perf FROM weekly_toppers)<>12 THEN RAISE EXCEPTION 'duplicate, lost source or changed snapshot'; END IF; END $$"
    binary=pgserver.postgres.__globals__['POSTGRES_BIN_PATH']/'postgres'
    result=subprocess.run([str(binary),'--single','-D',str(cluster),'postgres'],input='\n'.join(' '.join(q.split())+';' for q in schema+queries+[assertions])+'\n',text=True,capture_output=True,timeout=30)
    assert result.returncode==0 and 'ERROR:' not in result.stderr,result.stderr


def test_retired_workflow_removed_and_both_reports_have_db_secret():
    root=Path(__file__).resolve().parents[1]
    assert not (root/'.github/workflows/weekly_run_opvolg.yml').exists()
    for name in ['weekly_run.yml','weekly_run_x.yml']:
        text=(root/'.github/workflows'/name).read_text()
        assert 'SUPABASE_DB_URL: ${{ secrets.SUPABASE_DB_URL }}' in text
        assert 'psycopg2-binary' in text


@pytest.mark.parametrize('suffix', ['a','x'])
def test_partial_parameters_complete_report_and_keep_database_writes(monkeypatch,tmp_path,suffix):
    monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w,'weekgrenzen',lambda:(MON,FRI))
    monkeypatch.setattr(w,'laad_lijsten',lambda *_:{'tickers_01.txt':['A','B']})
    idx=pd.bdate_range(end='2026-11-06',periods=260)
    close=pd.Series(range(100,360),index=idx,dtype=float)
    hist=pd.DataFrame({'Close':close,'Open':close,'High':close+1,'Low':close-1,'Volume':100})
    def load(self,tickers):
        if self.historie:
            self.frames={'A':hist,'B':pd.DataFrame()}
    monkeypatch.setattr(w.WeekKoersen,'laad',load)
    monkeypatch.setattr(w.WeekKoersen,'performance',lambda *_:[dict(ticker=t,perf=20,
        basisdatum='2026-11-06',einddatum='2026-11-13') for t in ['A','B']])
    selected,parameters,messages=[],[],[]
    monkeypatch.setattr(db,'log_report_toppers',lambda rows:selected.extend(rows))
    monkeypatch.setattr(db,'log_weekly_topper_parameters',lambda rows:parameters.extend(rows))
    monkeypatch.setattr(db,'haal_laatste_rank_scores',lambda *_:{})
    monkeypatch.setattr(w,'meld',messages.append)
    monkeypatch.setattr(w,'send_email',lambda *_:None)
    w.maak_rapport(suffix)
    assert {r['ticker'] for r in selected}=={'A','B'}
    assert [r['ticker'] for r in parameters]==['A']
    assert all(r['bronselectie']==suffix for r in selected)
    path=tmp_path/('weekly/laatste_toppers_x.json' if suffix=='x' else 'weekly/laatste_toppers.json')
    assert {r['ticker'] for r in json.loads(path.read_text())}=={'A','B'}
    assert 'beschikbare parameters opgeslagen' in messages[-1]
    assert 'ontbreken voor: B' in messages[-1] and 'run is geslaagd' in messages[-1]
    assert not any('opslag mislukt' in m for m in messages)


def test_actual_database_failure_still_propagates(monkeypatch):
    def fail(rows):
        raise RuntimeError('database niet bereikbaar')
    monkeypatch.setattr(db,'log_report_toppers',fail)
    with pytest.raises(RuntimeError,match='database niet bereikbaar'):
        store.bewaar_selecties([dict(ticker='A',beurs='X',type='stijger',week_perf=20)],MON,FRI)
