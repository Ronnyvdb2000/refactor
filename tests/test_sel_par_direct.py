from datetime import date, datetime, timezone
from pathlib import Path
import pandas as pd
import pytest
from maintenance.sel_par_direct import labels, closed_history, technical_values, fundamental_values, TECH


def history(periods=80):
    days = pd.bdate_range('2026-01-05',periods=periods)
    return pd.DataFrame({'Close':range(100,100+periods),'Stock Splits':0.0},index=days)


def row(**kw):
    return dict(ticker='TEST',datum='2026-01-05',koers_selectiedatum=100.,**kw)


def test_actual_session_dates_and_no_future_labels():
    hist=history()
    values, dates=labels(hist,row(),date(2026,1,19))
    assert values['koers_5d']==105
    assert values['rendement_10d_pct']==pytest.approx(10)
    assert dates=={'5':'2026-01-12','10':'2026-01-19'}
    assert 'koers_20d' not in values


def test_weekend_selection_counts_subsequent_sessions():
    r=row();r['datum']='2026-01-10';r['koers_selectiedatum']=104
    values,dates=labels(history(),r,date(2026,2,1))
    assert dates['5']=='2026-01-16'
    assert values['koers_5d']==109


def test_conflicting_or_invalid_entry_never_makes_labels():
    r=row();r['koers_selectiedatum']=None
    assert labels(history(),r,date(2026,4,30))==({}, {})


def test_existing_labels_are_not_recalculated():
    values,dates=labels(history(),row(koers_5d=999.,rendement_5d_pct=5.),date(2026,4,30))
    assert 'koers_5d' not in values and '5' not in dates


def test_mismatching_stored_close_blocks_missing_return_fill():
    values,_=labels(history(),row(koers_5d=999.),date(2026,4,30))
    assert 'rendement_5d_pct' not in values


def test_split_basis_is_preserved_and_later_change_blocks():
    hist=history();hist['Close']=hist.Close/2
    hist.loc[hist.index[3],'Stock Splits']=2
    values,_=labels(hist,row(),date(2026,4,30))
    assert values['rendement_basis_koers']==50
    assert values['rendement_5d_pct']==pytest.approx(5)
    with pytest.raises(ValueError,match='Splitbasis'):
        labels(hist,row(rendement_basis_koers=100),date(2026,4,30))


def test_split_with_imported_unknown_basis_requires_review():
    hist=history();hist.loc[hist.index[3],'Stock Splits']=2
    with pytest.raises(ValueError,match='onbekende splitbasis'):
        labels(hist,row(koers_5d=105.),date(2026,4,30))


def test_duplicate_dates_and_large_history_gap_rejected():
    hist=history()
    with pytest.raises(ValueError):
        closed_history(pd.concat([hist,hist.iloc[:1]]),date(2026,4,30))
    hist=hist.drop(hist.index[2:15])
    assert labels(hist,row(),date(2026,4,30))==({}, {})


def test_technicals_use_only_closed_asof_bar_and_sufficient_warmup():
    days=pd.bdate_range('2025-01-01',periods=300)
    features=pd.DataFrame({name.upper():1. for name in TECH},index=days)
    selected=days[260]
    out=technical_values(features,selected.date())
    assert out['tech_koersdatum']==selected.date()
    assert technical_values(features,days[100].date())=={}
    features.loc[selected,'RSI14']=float('nan')
    assert technical_values(features,selected.date())=={}


def test_direct_seed_has_no_legacy_feature_store_reads():
    sql=Path('maintenance/sql/20261122_sel_par_direct.sql').read_text()
    for table in ('generieke_technicals','generieke_fundamentals','forward_returns'):
        assert 'JOIN public.'+table not in sql
        assert 'FROM public.'+table not in sql
    assert 's.koers::double precision AS koers_selectiedatum' in sql


def test_fundamentals_observation_is_never_backdated():
    observed=datetime(2026,11,22,10,tzinfo=timezone.utc)
    out=fundamental_values({'market_cap':1000,'fcf_growing':True},'2026-11-20',observed)
    assert out['fund_observatiedatum']==date(2026,11,22)
    assert out['fund_bijgewerkt_op']==observed
    assert out['fundamentals_tijdig_bekend'] is False
    assert out['fund_fcf_growing'] is True


def test_labels_produce_native_python_numbers_for_psycopg2():
    from psycopg2.extensions import adapt
    values,_=labels(history(),row(),date(2026,4,30))
    for key,value in values.items():
        if key.startswith(('koers_','rendement_')):
            assert type(value) is float,(key,type(value))
            assert b'np.' not in adapt(value).getquoted()


def test_database_boundary_normalizes_numpy_scalars_without_changing_bool_or_dates():
    import numpy as np
    from maintenance.sel_par_direct import database_value,update_row
    from psycopg2.extensions import adapt
    assert type(database_value(np.float64(1.5))) is float
    assert type(database_value(np.int64(3))) is int
    assert database_value(np.bool_(True)) is True
    assert database_value(np.float64('nan')) is None
    assert database_value(float('inf')) is None
    captured=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,args):captured.extend(args)
    class Conn:
        def cursor(self):return Cursor()
    update_row(Conn(),{'ticker':'TEST','datum':'2026-11-20'},
               {'rendement_5d_pct':np.float64(3.5),'technicals_aanwezig':np.bool_(True)})
    assert captured==[3.5,True,'TEST','2026-11-20']
    assert all(not isinstance(v,np.generic) for v in captured)
    assert all(b'np.' not in adapt(v).getquoted() for v in captured)


@pytest.mark.parametrize("label_review", [False, True])
def test_direct_pipeline_reuses_one_history_and_fund_snapshot_per_ticker(monkeypatch,label_review):
    import yfinance
    import maintenance.sel_par_direct as direct
    from scripts import bouw_generieke_technicals as technicals
    from scripts import bouw_generieke_fundamentals as fundamentals
    days=pd.bdate_range('2025-01-01','2026-11-20')
    hist=pd.DataFrame({'Close':100.,'Open':99.,'High':101.,'Low':98.,
                       'Volume':1000.,'Stock Splits':0.},index=days)
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,query,params):
            assert 'FROM public.sel_par' in query
        def fetchall(self):
            return [dict(ticker='TEST',datum=d,koers_selectiedatum=100.)
                    for d in ('2026-10-01','2026-10-02')]
    class Conn:
        def cursor(self,**kw): return Cursor()
        def commit(self): pass
        def rollback(self): raise AssertionError('Unexpected rollback')
    calls=[]
    class Ticker:
        def __init__(self,ticker): self.ticker=ticker
        def history(self,**kw): calls.append(self.ticker); return hist.copy()
    monkeypatch.setattr(yfinance,'Ticker',Ticker)
    monkeypatch.setattr(technicals,'bereken_indicatoren',lambda h,i:pd.DataFrame(
        {key.upper():1. for key in TECH},index=h.index))
    fundcalls=[]
    def fetch(ticker): fundcalls.append(ticker);return {'market_cap':1000},'ok'
    monkeypatch.setattr(fundamentals,'haal_fundamentals_op',fetch)
    writes=[]
    monkeypatch.setattr(direct,'update_row',lambda conn,row,values,dates,status:writes.append(values))
    monkeypatch.setattr(direct.time,'sleep',lambda _:None)
    if label_review:
        def needs_review(*args): raise direct.LabelReview('splitbasis')
        monkeypatch.setattr(direct,'labels',needs_review)
    report=direct.enrich(Conn(),date(2026,11,20))
    assert report['complete_execution'] is True
    assert report['technical_rows']==2 and report['fundamental_rows']==2
    assert report['label_rows']==(0 if label_review else 2)
    assert len(report['review'])==(2 if label_review else 0)
    assert calls==['^GSPC','TEST'] and fundcalls==['TEST']
    assert all(w['fundamentals_tijdig_bekend'] is False for w in writes)
