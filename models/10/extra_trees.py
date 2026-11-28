"""Fixed Extra Trees return regression; no production model publication."""
import hashlib

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor

from models.elastic_net import FEATURES

RECIPE = dict(n_estimators=300, max_depth=8, min_samples_leaf=10,
              max_features=0.8, bootstrap=False, criterion='squared_error',
              random_state=42, n_jobs=2)


def fit_model(train):
    x=train[FEATURES]
    y=train['target'].to_numpy(dtype=float)
    if not np.isfinite(x.to_numpy(dtype=float)).all() or not np.isfinite(y).all():
        raise ValueError('Extra Trees vereist eindige trainingsinvoer')
    if len(y)<2 or np.ptp(y)==0:
        raise ValueError('Extra Trees vereist variatie in het trainingsrendement')
    # No preprocessing fitted on current candidates; trees use raw feature units.
    return ExtraTreesRegressor(**RECIPE).fit(x,y)


def diagnostics(model):
    fingerprint=hashlib.sha256()
    for estimator in model.estimators_:
        tree=estimator.tree_
        for values in (tree.feature,tree.threshold,tree.children_left,tree.children_right,tree.value):
            fingerprint.update(values.tobytes())
    return dict(trees=len(model.estimators_),
                depths=[int(t.tree_.max_depth) for t in model.estimators_],
                nodes=[int(t.tree_.node_count) for t in model.estimators_],
                feature_importances=dict(zip(FEATURES,model.feature_importances_.tolist())),
                importance_method='training_impurity_reduction_not_out_of_sample_evidence',
                tree_structure_hash=fingerprint.hexdigest())
