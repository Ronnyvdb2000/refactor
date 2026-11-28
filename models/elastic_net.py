"""Fixed Elastic Net regression recipe for isolated historical research."""
import warnings

import numpy as np
from sklearn.compose import TransformedTargetRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import ElasticNet
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = ['atr14','atr14_pct','rsi14','ibs','ma50','ma200','pct_from_ma50',
            'pct_from_ma200','vol_ratio_20d','high52w','pct_from_high52w']
RECIPE = dict(alpha=0.01, l1_ratio=0.5, max_iter=50000, tol=1e-6,
              fit_intercept=True, selection='cyclic')


def fit_model(train):
    """Both scalers fit only on this cohort's mature training observations.

    Standardizing the target makes the fixed penalty independent of its unit.
    Predictions are inverse-transformed to adjusted-return percentage points.
    """
    x = train[FEATURES]
    y = train['target'].to_numpy(dtype=float)
    if not np.isfinite(x.to_numpy(dtype=float)).all() or not np.isfinite(y).all():
        raise ValueError('Elastic Net vereist eindige trainingsinvoer')
    if len(y) < 2 or np.ptp(y) == 0:
        raise ValueError('Elastic Net vereist variatie in het trainingsrendement')
    model = Pipeline([
        ('features', StandardScaler()),
        ('returns', TransformedTargetRegressor(
            regressor=ElasticNet(**RECIPE), transformer=StandardScaler()))])
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        model.fit(x, y)
    return model


def diagnostics(model):
    target = model.named_steps['returns']
    reg = target.regressor_
    return dict(
        coefficients=dict(zip(FEATURES, (reg.coef_ * target.transformer_.scale_[0]).tolist())),
        coefficient_units='return_percentage_points_per_training_feature_standard_deviation',
        intercept_return_pct=float(target.transformer_.inverse_transform([[reg.intercept_]])[0,0]),
        feature_means=model.named_steps['features'].mean_.tolist(),
        feature_scales=model.named_steps['features'].scale_.tolist(),
        target_mean=float(target.transformer_.mean_[0]),
        target_scale=float(target.transformer_.scale_[0]),
        nonzero_coefficients=int(np.count_nonzero(reg.coef_)),
        iterations=int(reg.n_iter_), dual_gap=float(reg.dual_gap_))
