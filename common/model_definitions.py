"""Ordered ML input contracts. Tuples prevent accidental cross-model mutation.

V4 and the parameter model deliberately retain their own feature contracts.
Callers keep list copies where pandas or persisted bundles require lists.
"""
ML_FEATURES = ('atr14', 'atr14_pct', 'rsi14', 'ibs', 'ma50', 'ma200',
               'pct_from_ma50', 'pct_from_ma200', 'vol_ratio_20d',
               'high52w', 'pct_from_high52w')
RELATIVE_ML_FEATURES = ('atr14_pct', 'rsi14', 'ibs', 'pct_from_ma50',
                        'pct_from_ma200', 'vol_ratio_20d', 'pct_from_high52w')
ML_HORIZONS = ('10d', '30d', '60d')
TOP_SCORE_FRACTION = 0.20
BASELINE_FEATURE = 'pct_from_ma50'


def parameter_scoring_defaults():
    """Fresh copy used by live ModelConfig and the frozen paper protocol."""
    return dict(top_n=10, min_ic=0.02, rolling_dagen=28, fallback_dagen=90,
                embargo_dagen=25, ic_max_abs=0.15, min_datums_ic=10,
                max_data_leeftijd=5)
