import logging
from datetime import date
import pandas as pd
import pytest
from weekly import download as d
from weekly import shared as w


def test_market_context_not_us_fallback():
    assert w.normaliseer_lijstticker('tickers_043a.txt','AMD')=='AMD.DE'
    assert w.normaliseer_lijstticker('tickers_043x.txt','SAP.DE')=='SAP.DE'
    assert w.normaliseer_lijstticker('tickers_048a.txt','AMD')=='AMD'
    assert w.normaliseer_lijstticker('tickers_041a.txt','RYA.I')=='RYA.IR'
    assert w.normaliseer_lijstticker('tickers_051a.txt','CDPROJEKT.WA')=='CDR.WA'


def test_missing_batch_has_only_one_batch_retry(monkeypatch):
    calls=[]
    monkeypatch.setattr(d.time,'sleep',lambda *_:None)
    monkeypatch.setattr(d.yf,'download',lambda **kw:calls.append(kw) or pd.DataFrame())
    assert d.batch_download(['BAD1','BAD2'],start='2026-10-26',end='2026-11-14').empty
    assert len(calls)==2
    assert all(c['tickers']==['BAD1','BAD2'] and c['threads']==2 for c in calls)


def test_rate_limit_stops_after_one_cooldown(monkeypatch):
    calls=[]; pauses=[]
    logger=logging.getLogger('yfinance')
    old=logger.handlers[:],logger.propagate,logger.level
    def download(**kw):
        calls.append(kw)
        logger.error("YFRateLimitError('Too Many Requests. Rate limited.')")
        return pd.DataFrame()
    monkeypatch.setattr(d.yf,'download',download)
    monkeypatch.setattr(d.time,'sleep',pauses.append)
    with pytest.raises(d.DownloadOnderbroken,match='blijft'):
        d.batch_download(['A'],start='2026-10-26',end='2026-11-14')
    assert len(calls)==2 and pauses==[15]
    assert old==(logger.handlers,logger.propagate,logger.level)


def test_batch_multindex_and_missing_retry(monkeypatch):
    idx=pd.to_datetime(['2026-11-06','2026-11-13'])
    frame=pd.DataFrame({'Open':[100,110],'High':[101,111],'Low':[99,109],'Close':[100,110],'Volume':[100,100]},index=idx)
    calls=[]
    def download(**kw):
        calls.append(kw['tickers'])
        return pd.concat({'A':frame},axis=1) if len(calls)==1 else frame
    monkeypatch.setattr(d.yf,'download',download)
    monkeypatch.setattr(d.time,'sleep',lambda *_:None)
    result=d.batch_download(['A','B'],start='2026-10-26',end='2026-11-14')
    assert calls==[['A','B'],['B']]
    assert set(result.Ticker)=={'A','B'}


def test_bad_coverage_cannot_publish_json(monkeypatch,tmp_path):
    monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w,'weekgrenzen',lambda:(date(2026,11,9),date(2026,11,13)))
    monkeypatch.setattr(w,'laad_lijsten',lambda *_:{'tickers_051a.txt':['A','B','C','D']})
    monkeypatch.setattr(w.WeekKoersen,'laad',lambda *_:None)
    monkeypatch.setattr(w.WeekKoersen,'performance',lambda *_:[dict(ticker='A',perf=10,basisdatum='2026-11-06',einddatum='2026-11-13')])
    messages=[]
    monkeypatch.setattr(w,'meld',messages.append)
    monkeypatch.setattr(w,'send_email',lambda *a:True)
    old=tmp_path/'laatste_toppers.json';old.write_text('old snapshot')
    with pytest.raises(RuntimeError,match='80%'):
        w.maak_rapport()
    assert old.read_text()=='old snapshot'
    assert 'ONVOLLEDIG' in messages[0] and 'geen ranglijst' in messages[0]
    assert (tmp_path/'weekly_dekking_a.csv').exists()


def test_report_rate_limit_keeps_snapshot_and_notifies(monkeypatch,tmp_path):
    monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w,'laad_lijsten',lambda *_:{'tickers_01.txt':['A']})
    def abort(*_):raise d.DownloadOnderbroken('Yahoo rate limit')
    monkeypatch.setattr(w.WeekKoersen,'laad',abort)
    messages=[];monkeypatch.setattr(w,'meld',messages.append)
    with pytest.raises(d.DownloadOnderbroken):w.maak_rapport()
    assert not (tmp_path/'laatste_toppers.json').exists()
    assert messages and 'ONVOLLEDIG' in messages[0]


def test_report_low_coverage_never_writes_database(monkeypatch,tmp_path):
    from weekly import selection_storage as storage
    monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w,'laad_lijsten',lambda *_:{'tickers_051a.txt':['A','B','C','D']})
    monkeypatch.setattr(w.WeekKoersen,'laad',lambda *_:None)
    monkeypatch.setattr(w.WeekKoersen,'performance',lambda *_:[dict(ticker='A',perf=20)])
    monkeypatch.setattr(w,'meld',lambda *_:None)
    monkeypatch.setattr(w,'send_email',lambda *_:None)
    monkeypatch.setattr(storage,'bewaar_selecties',lambda *_:pytest.fail('incomplete scan reached database export'))
    with pytest.raises(RuntimeError,match='weekdata'):w.maak_rapport()


def test_long_history_only_selected_tickers(monkeypatch):
    from weekly import weekly_report_opvolg as o
    requested=[]
    class Hist:
        def __init__(self,*args,**kw):
            assert kw['historie'] is True
            self.frames={'A':pd.DataFrame()}
        def laad(self,tickers):requested.extend(tickers)
    monkeypatch.setattr(o,'WeekKoersen',Hist)
    monkeypatch.setattr(o.weekly_db,'log_weekly_toppers',lambda rows:len(rows))
    monkeypatch.setattr(o.weekly_db,'log_weekly_topper_parameters',lambda rows:len(rows))
    monkeypatch.setattr(o.weekly_db,'haal_laatste_rank_scores',lambda *_:{})
    monkeypatch.setattr(o,'haal_technische_parameters',lambda *_:[dict(datum=date(2026,11,9),close=100)])
    e=dict(ticker='A',beurs='X',lijst='test',type='stijger',rang=1,week_perf=10)
    o.verwerk_weekly_db_export([e],date(2026,11,9),date(2026,11,13),None)
    assert requested==['A']


def test_empty_lists_and_one_small_list_gap_are_not_failures():
    assert w.controleer_dekking('empty', [], [])
    assert w.controleer_dekking('Oslo', ['A','B','C','D'], [{},{},{}])
    assert not w.controleer_dekking('Oslo', ['A','B','C','D'], [{},{}])
    assert not w.controleer_dekking('missing', ['A'], [])
    assert not w.controleer_dekking('Frankfurt', list(range(1418)), [{}]*165)
