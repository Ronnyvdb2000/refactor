from copy import deepcopy
from datetime import date
from unittest.mock import MagicMock
from pathlib import Path

import pandas as pd
import pytest
import yaml

from analyses import backtest_europe as eu
from analyses import europe_execution as execution
from analyses import europe_market as market
from analyses import paper_trial as usd
from analyses.backtest_europe_telegram import message
from analyses.backtest_telegram import notify


def quote(currency='EUR',price=100.,days=('2026-09-17','2026-09-18')):
    return dict(currency=currency,bars=[dict(day=d,open=price,close=price) for d in days])


def snapshot():
    p=execution.plan([dict(ticker=f'T{i}.AS',score=float(i),currency='EUR') for i in range(10)])
    p.update(selection_date='2026-09-15',decided_at='2026-09-16T06:00:00+00:00',
             sessions=[str(d.date()) for d in pd.bdate_range('2026-09-17',periods=10)])
    return p


def test_common_calendar_excludes_local_holidays_and_every_session_is_open():
    import exchange_calendars as xcals
    c=market.CommonCalendar('2026-08-01','2027-03-01')
    days=c.sessions_in_range('2026-08-28','2026-09-04')
    assert pd.Timestamp('2026-08-31') not in days  # UK holiday, not a continental holiday.
    assert pd.Timestamp('2026-09-01') in days
    for name in set(market.CALENDARS.values()):
        assert all(xcals.get_calendar(name).is_session(d) for d in days)


@pytest.mark.parametrize('ticker,venue,allowed',[
    ('ASML.AS','041 Benelux Ierland',True),('SHEL.L','045 Londen',True),
    ('SAP.DE','043 Frankfurt',True),('KRKG.LJ','059 Oostenrijk Slovenie Slovakije',True),
    ('AAPL','048 Nasdaq/NYSE',False),('ABC.TO','047 Toronto',False),
    ('BAD.UNKNOWN','042 Parijs',False),('US.AS','048 Nasdaq/NYSE',False)])
def test_universe_is_predeclared_from_venue_and_suffix(ticker,venue,allowed):
    assert market.eligible(ticker,venue)==allowed


@pytest.mark.parametrize('currency',['GBp','GBX'])
def test_pence_are_converted_to_pounds_before_eur_fx(currency):
    value=market.convert('UK.L',quote(currency,100.),{'GBPEUR=X':quote('EUR',1.2)})
    assert value['bars'][0]['open']==pytest.approx(1.2)
    assert value['unit_factor']==.01 and value['fx_fee_bps']==5


def test_fx_moves_affect_eur_return_and_rates_are_not_forward_filled():
    fx=quote('EUR',1.);fx['bars'][1]['close']=1.1
    converted=market.convert('A.SW',quote('CHF'),{'CHFEUR=X':fx})
    assert converted['bars'][1]['close']/converted['bars'][0]['open']-1==pytest.approx(.1)
    fx['bars']=fx['bars'][:1]
    missing=market.convert('A.SW',quote('CHF'),{'CHFEUR=X':fx})
    assert len(missing['bars'])==1 and missing['missing_conversion_days']==['2026-09-18']


def test_eur_identity_missing_currency_and_duplicate_fx_are_explicit():
    original=quote();converted=market.convert('A.AS',original,{})
    assert original==quote() and converted['bars']==original['bars'] and converted['fx_fee_bps']==0
    assert market.convert('A.AS',quote(None),{})['error']=='unverified_or_unsupported_quote_currency'
    fx=quote();fx['bars'].append(fx['bars'][0])
    with pytest.raises(ValueError,match='Dubbele FX'):
        market.convert('UK.L',quote('GBP'),{'GBPEUR=X':fx})


def test_eur_execution_matches_usd_arithmetic_when_there_is_no_fx():
    p=snapshot();tickers=[*p['universe'],market.BENCHMARK]
    prices={(t,d):dict(open=100.,close=105.,currency='EUR',dividend=0.,split=0.) for t in tickers for d in p['sessions']}
    config=dict(horizon=10,top_n=10,commission_bps=5,slippage_bps=10)
    capital=dict.fromkeys(p['books'],100000.)
    result=execution.simulate(p,prices,capital,config,as_of='2026-11-01')
    dollar={k:{**v,'currency':'USD'} for k,v in prices.items()}
    assert result==usd.simulate(p,dollar,capital,config,as_of='2026-11-01')


def test_fx_costs_charged_only_on_entry_and_exit_without_daily_reconversion_fee():
    p=snapshot();prices={(t,d):dict(open=100.,close=100.,currency='EUR',dividend=0.,split=0.,fx_fee_bps=5)
                        for t in [*p['universe'],market.BENCHMARK] for d in p['sessions']}
    config=dict(horizon=10,top_n=10,commission_bps=0,slippage_bps=0)
    result=execution.simulate(p,prices,dict.fromkeys(p['books'],100000.),config,as_of='2026-11-01')
    assert result['ending']['a_par:a_par_model_v1']==pytest.approx(100000*(1-.0005)/(1+.0005))


def test_missing_stock_or_fx_keeps_fixed_cash_slot_and_benchmark_cannot_be_cash():
    p=snapshot();cache={t:quote(days=p['sessions']) for t in [*p['universe'],market.BENCHMARK]}
    cache['T9.AS']['bars']=cache['T9.AS']['bars'][:-1]
    result=eu.evaluate([p],cache,date(2026,11,1))
    a=result['periods'][0]['allocations']['a_par:a_par_model_v1']
    assert a['purchased']==9 and a['invested_budget']==90000
    cache[market.BENCHMARK]['bars']=[]
    with pytest.raises(ValueError,match='benchmark mist'):eu.evaluate([p],cache,date(2026,11,1))


@pytest.mark.parametrize('bad',['currency','negative'])
def test_invalid_benchmark_never_becomes_an_apparently_flat_index(bad):
    p=snapshot();cache={t:quote(days=p['sessions']) for t in [*p['universe'],market.BENCHMARK]}
    if bad=='currency':cache[market.BENCHMARK]['currency']='USD'
    else:cache[market.BENCHMARK]['bars'][0]['open']=-1.
    with pytest.raises(ValueError,match='benchmark mist'):eu.evaluate([p],cache,date(2026,11,1))


class WeekdayCalendar:
    def sessions_in_range(self,start,end):return pd.bdate_range(start,end)


def training_data():
    days=pd.bdate_range('2026-08-03','2026-10-30')
    frame=pd.DataFrame([dict(ticker=f'A{i}.AS',datum=d,factor=float(i),fwd_ret_10d=float(i)) for d in days for i in range(25)])
    meta=pd.DataFrame([dict(ticker=r.ticker,datum=r.datum,beurs='041 Benelux Ierland',
                            label_date=r.datum+pd.Timedelta(days=16),labels_review=None) for r in frame.itertuples()])
    return frame,meta


def test_global_model_recipe_keeps_only_mature_labels_and_nonoverlapping_european_plans(monkeypatch):
    frame,meta=training_data();meta.loc[meta.ticker=='A0.AS','beurs']='048 Nasdaq/NYSE'
    def fit(df,target,day,embargo,*args):
        mature=df.loc[df[target].notna()]
        assert all(mature.datum+pd.Timedelta(days=16)<day+pd.Timedelta(days=1))
        assert all(df.datum<=day)
        return pd.DataFrame([dict(parameter='factor',ic=.1,ic_abs=.1)]),'fixed',day-pd.Timedelta(days=53),day-pd.Timedelta(days=25)
    monkeypatch.setattr(eu.model,'ic_met_fallback',fit)
    plans=eu.prepare(frame,meta,date(2026,11,1),WeekdayCalendar())['plans']
    assert plans and all('A0.AS' not in p['universe'] for p in plans)
    assert plans[0]['books']['benchmark']==[market.BENCHMARK]
    for a,b in zip(plans,plans[1:]):assert b['selection_date']>a['sessions'][-1]


def test_reviewed_labels_never_train(monkeypatch):
    frame,meta=training_data();meta['labels_review']='review'
    def fit(df,target,day,*args):
        assert df[target].isna().all()
        return pd.DataFrame(),'none',day,day
    monkeypatch.setattr(eu.model,'ic_met_fallback',fit)
    assert not eu.prepare(frame,meta,date(2026,11,1),WeekdayCalendar())['plans']


def test_runner_freezes_source_and_ranks_before_eur_evaluation_and_pins_retry(monkeypatch):
    frame,meta=training_data();p=snapshot()
    frame['ticker']=frame.ticker.str.replace('A','T',regex=False)
    meta['ticker']=meta.ticker.str.replace('A','T',regex=False)
    selection=dict(plans=[p],raw_frame={},metadata={})
    parent=dict(run_key='frozen-source',config=eu.native.configuration('2026-11-27'),selection_snapshot=selection)
    original=deepcopy(parent)
    saved={t:quote(days=p['sessions']) for t in p['universe']}
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},None,parent]
    cur.fetchall.return_value=[dict(ticker=t,data=v) for t,v in saved.items()]
    monkeypatch.setattr(eu.native,'restore_inputs',lambda s:(frame,meta))
    monkeypatch.setattr(eu.native,'reconstruct_targets',lambda *args:(frame,meta))
    def prepared(*args,**kwargs):
        assert conn.commit.call_count>=2
        return dict(plans=[p],rows=len(frame),dates=frame.datum.nunique(),data_start='2026-08-03',data_end='2026-10-30')
    monkeypatch.setattr(eu,'prepare',prepared)
    provider=MagicMock(return_value=quote(days=p['sessions']))
    result=eu.run(conn,'europe-run',provider=provider)
    assert result['status']=='complete' and result['currency']=='EUR' and parent==original
    provider.assert_called_once_with(market.BENCHMARK,'2026-07-24','2026-11-27')
    writes=[c.args for c in cur.execute.call_args_list if c.args[0].startswith(('INSERT','UPDATE'))]
    assert all('public.research_backtest' in args[0] for args in writes)
    config=writes[0][1][2].adapted
    cur.reset_mock();cur.fetchone.side_effect=[{'locked':True},dict(config=config,status='complete',result=result),parent]
    assert eu.run(conn,'europe-run',provider=provider)==result
    assert cur.execute.call_args_list[2].args[1]==('frozen-source',)


def test_missing_benchmark_series_saves_partial_without_return(monkeypatch):
    frame,meta=training_data()
    parent=dict(run_key='source',config=eu.native.configuration('2026-11-27'),selection_snapshot={})
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},None,parent];cur.fetchall.return_value=[]
    monkeypatch.setattr(eu.native,'restore_inputs',lambda s:(frame,meta))
    result=eu.run(conn,'missing-input',provider=lambda *args:quote(None))
    assert result['status']=='partial' and result['books']=={} and market.BENCHMARK in result['quote_errors']


def test_eur_report_uses_durable_shared_transport_and_workflow_separates_secrets():
    p=snapshot();saved={t:quote(days=p['sessions']) for t in [*p['universe'],market.BENCHMARK]}
    result=eu.evaluate([p],saved,date(2026,11,1))
    row=dict(run_key='europe:test',status='complete',config=eu.configuration('2026-11-01'),result=result)
    text=message(row)
    assert '€100,000' in text and 'EXSA.DE' in text and '$' not in text and 'SPY' not in text
    assert len(text.encode('utf-16-le'))//2<3900
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},row,{'status':'claimed'}]
    def send(text,token,chat):
        assert conn.commit.call_count==1 and '€100,000' in text
        return 'sent',123
    assert notify(conn,'europe:test','token','chat',send,formatter=message)==1
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.load((root/'.github/workflows/backtest_europe.yml').read_text(),Loader=yaml.BaseLoader)
    calc=next(s for s in workflow['jobs']['europe']['steps'] if s.get('id')=='backtest')
    assert not any(k.startswith('TELEGRAM') for k in calc['env'])
