"""Een herrun voor dezelfde scoredatum mag bestaande horizons niet aanvullen."""
from unittest.mock import MagicMock, patch

import pandas as pd

from meta_models import a_par_model as par


def _conn(bestaande_horizons):
    cur = MagicMock()
    cur.fetchall.return_value = [(h,) for h in bestaande_horizons]
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn


def _picks():
    return pd.DataFrame(dict(ticker=['AAA', 'BBB'], koers=[10., 20.], score=[.3, .2]))


def test_existing_horizon_is_not_topped_up_but_new_horizon_is_written():
    picks = {'5d': _picks(), '10d': _picks()}
    with patch.object(par.db_logger, 'log_selecties_bulk', return_value=2) as bulk:
        par.schrijf_naar_selecties(_conn({'5d'}), picks, pd.Timestamp('2026-11-20'))
    rows = bulk.call_args.args[0]
    assert {r['parameters']['horizon'] for r in rows} == {'10d'}


def test_first_run_writes_all_horizons():
    picks = {'5d': _picks(), '10d': _picks()}
    with patch.object(par.db_logger, 'log_selecties_bulk', return_value=4) as bulk:
        par.schrijf_naar_selecties(_conn(set()), picks, pd.Timestamp('2026-11-20'))
    rows = bulk.call_args.args[0]
    assert {r['parameters']['horizon'] for r in rows} == {'5d', '10d'}
    assert len(rows) == 4
