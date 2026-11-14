from datetime import date, datetime, timezone
import importlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from weekly import shared as w
from weekly import weekly_rep_backtest as back
from weekly import weekly_report_opvolg as opvolg
from weekly import analyseer_weekly_correlaties as corr
from weekly import weekly_db_opvolg as db

ROOT = Path(__file__).resolve().parents[1]
MON, FRI = date(2026, 11, 16), date(2026, 11, 20)


def frame(index, prices):
    c = np.asarray(prices, dtype=float)
    return pd.DataFrame({'Open': c, 'High': c+1, 'Low': c-1, 'Close': c,
                         'Volume': np.full(len(c),1000.)}, index=pd.to_datetime(index))


@pytest.mark.parametrize('moment,start,end',[
    ('2026-11-13T22:00:00+00:00','2026-11-02','2026-11-06'),
    ('2026-11-13T23:00:00+00:00','2026-11-09','2026-11-13'),
    ('2026-11-14T20:00:00+01:00','2026-11-09','2026-11-13'),
    ('2026-11-16T09:00:00+00:00','2026-11-09','2026-11-13')])
def test_completed_week(moment,start,end):
    assert tuple(map(str,w.weekgrenzen(datetime.fromisoformat(moment)))) == (start,end)


def test_week_includes_monday_return_and_caches(monkeypatch):
    calls=[]
    def download(tickers, **kw):
        calls.append((tickers,kw))
        f=frame(['2026-11-13','2026-11-16','2026-11-20'],[100,110,121])
        return f.rename_axis('Date').reset_index().assign(Ticker='ABC')
    monkeypatch.setattr(w,'download_history',download)
    cache=w.WeekKoersen(MON,FRI)
    assert cache.performance(['ABC','ABC'])[0]['perf'] == pytest.approx(21)
    assert cache.performance(['ABC'])[0]['basisdatum']=='2026-11-13'
    assert len(calls)==1
    assert calls[0][1]['end']=='2026-11-21'
    assert calls[0][1]['request_timeout']==15


def test_no_stale_or_missing_baseline():
    cache=w.WeekKoersen(MON,FRI)
    cache.frames['OLD']=frame(['2026-10-01','2026-10-02'],[100,110])
    cache.frames['IPO']=frame(['2026-11-16','2026-11-20'],[100,110])
    assert cache.performance(['OLD','IPO'])==[]


def test_ranking_signs_and_worst_first():
    top,bottom,rest=w.selecteer([{'ticker':str(i),'perf':v} for i,v in enumerate([5,-1,-9,0,2])])
    assert list(top.perf)==[5,2]
    assert list(bottom.perf)==[-9,-1]
    assert list(rest.perf)==[0]


def test_telegram_preserved_and_chunks(monkeypatch):
    calls=[]
    monkeypatch.setattr(w,'send_telegram',lambda message,**kw:calls.append((message,kw)) or True)
    w.meld('A'*8000)
    assert len(calls)==3
    assert all(len(m)<=3900 and k['parse_mode'] is None for m,k in calls)


def test_peak_first_search_day_included():
    f=frame(pd.bdate_range('2026-01-01',periods=6),[100,100,200,201,202,203])
    past,day=back.vind_piekdag_en_snijd(f,4)
    assert day==f.index[2]
    assert past.index[-1]==f.index[1]


def test_old_json_rejected(monkeypatch,tmp_path):
    path=tmp_path/'top.json'
    path.write_text(json.dumps([{'beurs':'X','ticker':'A','week_perf':1}]))
    monkeypatch.setattr(back,'TOPPERS_BESTAND',path)
    with pytest.raises(ValueError,match='Ongedateerde'):
        back.laad_toppers()


def test_parameters_only_requested_week_and_causal():
    idx=pd.bdate_range(end='2026-11-20',periods=280)
    h=frame(idx,100+np.arange(280)*.05+np.sin(np.arange(280)))
    rows=opvolg.haal_technische_parameters(h,MON,FRI)
    assert len(rows)==5
    assert rows[0]['datum']==MON and rows[-1]['datum']==FRI
    earlier=opvolg.haal_technische_parameters(h.iloc[:-2],MON,FRI)
    assert rows[:3]==earlier
    assert not any('pe_ratio' in r or 'debt_to_ebitda' in r for r in rows)


def test_analysis_deduplicates_and_aligns_actual_target():
    toppers=pd.DataFrame([dict(ticker='A',beurs='X',week_startdatum=MON,week_perf=999,type=t)
                          for t in ['stijger','neutraal']])
    params=[]
    for day,close in [('2026-11-16',100),('2026-11-20',110)]:
        r={k:None for k in corr.GROEP_A_KOLOMMEN+corr.GROEP_BC_KOLOMMEN}
        r.update(ticker='A',beurs='X',week_startdatum=MON,datum=day,dag_index=pd.Timestamp(day).weekday()+1,close=close)
        params.append(r)
    result=corr.bouw_analysetabel(toppers,pd.DataFrame(params))
    assert len(result)==1
    assert result.week_perf.iloc[0]==pytest.approx(10)
    old=toppers.assign(week_startdatum=date(2026,11,9))
    assert corr.bouw_analysetabel(old,pd.DataFrame(params)).empty


def test_constant_and_infinite_data_not_significant():
    rows=corr.analyseer_familie(pd.DataFrame({'week_perf':[1]*40,'x':[float('inf')]+[2]*39}),['x'],'test')
    assert rows[0]['p_waarde'] is None
    assert not rows[0]['significant']


@pytest.mark.parametrize('filename,module',[
    ('weekly_run.yml','weekly_report'),('weekly_run_x.yml','weekly_report_x'),
    ('weekly_run_opvolg.yml','weekly_report_opvolg'),('weekly_runV2.yml','weekly_rep_backtest'),
    ('analyse_correlaties_weeklytop.yml','analyseer_weekly_correlaties')])
def test_workflows_import_and_run_module(filename,module):
    workflow=yaml.safe_load((ROOT/'.github/workflows'/filename).read_text())
    steps=next(iter(workflow['jobs'].values()))['steps']
    run=next(s for s in steps if s.get('run')==f'python -m weekly.{module}')
    assert 'TELEGRAM_TOKEN' in run['env']
    assert 'TELEGRAM_CHAT_ID' in run['env']
    importlib.import_module('weekly.'+module)
    assert not (ROOT/(module+'.py')).exists()


def test_backtest_waits_for_success_and_json_writers_serialize():
    def read(name):return yaml.safe_load((ROOT/'.github/workflows'/name).read_text())
    backtest=read('weekly_runV2.yml')
    assert 'schedule' not in backtest.get('on',backtest.get(True))
    assert 'workflow_run' in backtest.get('on',backtest.get(True))
    a,x=read('weekly_run.yml'),read('weekly_run_x.yml')
    assert a['concurrency']['group']==x['concurrency']['group']
    for config in (a,x):
        steps=next(iter(config['jobs'].values()))['steps']
        save=next(s for s in steps if s.get('name','').startswith('Opslaan'))
        assert save['if'].startswith('success()')
        assert '-X ours' not in save['run']
        assert 'git pull --rebase origin main' in save['run']


@pytest.mark.parametrize('suffix',['a','x'])
def test_report_writes_dated_json_and_keeps_notifications(monkeypatch,tmp_path,suffix):
    monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w,'weekgrenzen',lambda:(MON,FRI))
    monkeypatch.setattr(w,'laad_lijsten',lambda _: {'tickers_041'+suffix+'.txt':['ABC']})
    def download(tickers,**kw):
        return frame(['2026-11-13','2026-11-16','2026-11-20'],[100,110,121]).rename_axis('Date').reset_index().assign(Ticker='ABC')
    monkeypatch.setattr(w,'download_history',download)
    messages=[]
    monkeypatch.setattr(w,'meld',messages.append)
    monkeypatch.setattr(w,'send_email',lambda *a,**kw:True)
    w.maak_rapport(suffix)
    pad=tmp_path/('laatste_toppers_x.json' if suffix=='x' else 'laatste_toppers.json')
    result=json.loads(pad.read_text())
    assert result[0]['data_version']==2
    assert result[0]['week_startdatum']==str(MON)
    assert result[0]['week_perf']==21
    assert messages and '+21.00%' in messages[0]


def test_backtest_download_anchored_and_no_current_info(monkeypatch):
    calls=[]
    idx=pd.bdate_range(end='2026-11-27',periods=560)
    h=frame(idx,100+np.arange(560)*.05+np.sin(np.arange(560)))
    def download(*args,**kw):
        calls.append(kw)
        return h.copy()
    monkeypatch.setattr(back.yf,'download',download)
    monkeypatch.setattr(back.yf,'Ticker',lambda *_:pytest.fail('Current fundamentals must not enter historical backtest'))
    r=back.analyseer('ABC',10,str(FRI))
    assert r is not None
    assert r['laatste_datum']<r['piek_datum']<=str(FRI)
    assert calls[0]['end']=='2026-11-21'
    assert r['short_pct_float'] is None


def test_future_invalid_bar_cannot_change_parameters():
    idx=pd.bdate_range(end='2026-11-20',periods=280)
    h=frame(idx,100+np.arange(280)*.05+np.sin(np.arange(280)))
    future=frame(['2026-11-23'],[200])
    future.loc[:,'High']=-1
    assert opvolg.haal_technische_parameters(h,MON,FRI)==opvolg.haal_technische_parameters(pd.concat([h,future]),MON,FRI)
