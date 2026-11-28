from unittest.mock import MagicMock,patch
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from common.model_archive import parameter_rows,archive_meta_fold
from meta_models.a_meta_label_classifier import walk_forward,walk_forward_per_strategie


def data():
    dates=pd.bdate_range('2026-01-01',periods=12)
    return pd.DataFrame([dict(ticker=f'T{i}',strategie='strat',datum=d,
                             label_end=d+pd.offsets.BDay(1),x=float(i),label=float(i>=2),fwd_ret=5 if i>=2 else -5)
                         for d in dates for i in range(4)])


def test_logistic_coefficients_keep_signed_value_without_ic_normalization():
    row=parameter_rows(pd.DataFrame([dict(parameter='x',coefficient=-2.3)]),kind='coefficient')[0]
    assert row['weight']==-2.3 and row['ic'] is None and row['kind']=='coefficient'


def test_each_fold_supplies_actual_prior_training_and_fitted_scaler_to_archive():
    seen=[]
    def capture(test,train,model,scaler,features):
        assert train.datum.max()<test.datum.min()-pd.Timedelta(days=2)
        assert features==['x']
        np.testing.assert_allclose(test.proba,model.predict_proba(scaler.transform(test[['x']].values))[:,1])
        seen.append(test.datum.min())
    result,_=walk_forward(data(),['x'],2,8,4,'logreg',archive_fold=capture)
    assert len(seen)==result.datum.nunique()>0


def test_strategy_callbacks_have_separate_scope():
    frame=data();other=frame.assign(strategie='other')
    seen=[]
    def factory(scope):
        return lambda test,*args:seen.append((scope,set(test.strategie)))
    results,_=walk_forward_per_strategie(pd.concat([frame,other]),['x'],2,8,4,'logreg',archive_factory=factory)
    assert set(results)=={'other','strat'}
    assert seen and all(values=={scope} for scope,values in seen)


def test_fold_archive_averages_strategy_scores_and_marks_conditioned_backtest():
    train=data().iloc[:8]
    scaler=StandardScaler().fit(train[['x']].values)
    estimator=LogisticRegression().fit(scaler.transform(train[['x']].values),train.label)
    test=pd.DataFrame(dict(ticker=['A','A','B'],datum=pd.to_datetime(['2026-03-01']*3),proba=[.2,.8,.3]))
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchall.return_value=[
        ('A',pd.Timestamp('2026-03-01').date(),100),('B',pd.Timestamp('2026-03-01').date(),50)]
    with patch('common.model_archive.record',return_value='id') as record:
        archive_meta_fold(conn,test,train,estimator,scaler,['x'],horizon=10,threshold=3,
                          model_type='logreg',scope='combined',batch_id='test:1')
    saved=record.call_args.args[1].set_index('ticker')
    assert saved.loc['A','score']==.5 and saved.loc['A','koers']==100
    kwargs=record.call_args.kwargs
    assert kwargs['mode']=='backtest' and kwargs['kind']=='coefficient'
    assert kwargs['config']['comparison_eligible'] is False
    assert kwargs['config']['outcome_conditioned'] is True
    assert kwargs['config']['scaler_scale']==scaler.scale_.tolist()
    conn.commit.assert_called_once()


def test_full_test_archive_keeps_middle_zone_and_dates_without_extreme_labels():
    train=data().iloc[:24]
    day=pd.Timestamp('2026-03-01')
    full_test=pd.DataFrame([dict(ticker=f'M{i}',strategie='strat',datum=day,x=float(i),fwd_ret=0.) for i in range(4)])
    seen=[]
    result,_=walk_forward(train,['x'],2,8,4,'logreg',
                          archive_fold=lambda test,*args:seen.append(test),
                          evaluation_candidates=full_test)
    assert result.empty  # No conditional extreme-label test rows on this date.
    assert len(seen)==1 and len(seen[0])==4
    assert seen[0].fwd_ret.eq(0).all() and seen[0].proba.notna().all()
