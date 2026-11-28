"""Actual label intervals, purged chronological boundaries and overlap reporting.

Calendar buffers remain additional separation, never a substitute for observed
label end dates. Unknown/malformed ends cannot authorize training or evaluation.
"""
import hashlib
import json

import pandas as pd

from .horizons import horizon_days
from .model_inputs import InputUnavailable,dates
from .feature_pipeline import load_feature_context


def label_column(horizon):
    return f'label_end_{horizon_days(horizon)}d'


def valid_labels(frame,horizon,*,date_column='datum'):
    column=label_column(horizon)
    if column not in frame:
        raise InputUnavailable(f'Werkelijke label-einddatums ontbreken: {column}')
    return valid_intervals(frame, column, date_column=date_column)


def valid_intervals(frame, end_column, *, date_column='datum'):
    if end_column not in frame:
        raise InputUnavailable(f'Werkelijke label-einddatums ontbreken: {end_column}')
    starts=dates(frame[date_column]);ends=dates(frame[end_column])
    valid=starts.notna() & ends.notna() & (ends>starts)
    if 'labels_review' in frame:valid &= frame.labels_review.isna()
    return valid


def known_labels(frame,horizon,boundary,*,date_column='datum'):
    edge=pd.Timestamp(boundary)
    if pd.isna(edge):raise InputUnavailable('Trainingsgrens ontbreekt')
    edge=edge.tz_localize(None).normalize() if edge.tzinfo else edge.normalize()
    context=load_feature_context()
    if context:
        edge=min(edge,pd.Timestamp(context['selection_cutoff'])+pd.Timedelta(days=1))
    return valid_labels(frame,horizon,date_column=date_column) & (dates(frame[label_column(horizon)])<edge)


def purge_before(frame,horizon,boundary):
    result=frame.loc[known_labels(frame,horizon,boundary)].copy()
    result.attrs.update(frame.attrs)
    result.attrs['purge']=dict(boundary=str(pd.Timestamp(boundary).date()),
                               input_rows=len(frame),known_rows=len(result),horizon=horizon_days(horizon))
    return result


def overlap_report(frame,horizon):
    if frame.empty:return dict(rows=0,dates=0,nonoverlapping_blocks=0,unknown_intervals=0)
    good=valid_labels(frame,horizon)
    periods=pd.DataFrame({'start':dates(frame.loc[good,'datum']),
                          'end':dates(frame.loc[good,label_column(horizon)])})
    blocks=0;last=None
    for start,end in periods.groupby('start').end.max().sort_index().items():
        if last is None or start>last:
            blocks+=1;last=end
    return dict(rows=len(frame),dates=int(dates(frame.datum).nunique()),nonoverlapping_blocks=blocks,
                unknown_intervals=int((~good).sum()),
                note='Non-overlapping windows are not a proof of statistical independence')


def input_fingerprint(frame):
    """Fingerprint the actual read frame, including temporal metadata, not a DB version claim."""
    columns=sorted(frame.columns)
    rows=frame[columns].to_json(orient='records',date_format='iso',double_precision=15)
    return hashlib.sha256(json.dumps(columns).encode()+rows.encode()).hexdigest()


def purge_interval_before(frame, boundary, *, end_column='label_end'):
    """Classifier readers carry the explicitly requested horizon as one end column."""
    edge=pd.Timestamp(boundary)
    if pd.isna(edge):raise InputUnavailable('Trainingsgrens ontbreekt')
    edge=edge.tz_localize(None).normalize() if edge.tzinfo else edge.normalize()
    context=load_feature_context()
    if context:
        edge=min(edge,pd.Timestamp(context['selection_cutoff'])+pd.Timedelta(days=1))
    good=valid_intervals(frame,end_column) & (dates(frame[end_column])<edge)
    return frame.loc[good].copy()


def purged_training(frame, horizon, boundary, *, target_mode='abs', min_per_date=10):
    """Compute relative training targets only from outcomes known at the boundary.

    Removing a late label after computing a cross-sectional median is insufficient:
    its unknown return could already have changed every other row's target.
    """
    result = purge_before(frame, horizon, boundary)
    if target_mode == 'excess':
        label = f'fwd_ret_{horizon_days(horizon)}d'
        counts = result.groupby('datum')[label].transform('count')
        result = result.loc[counts >= min_per_date].copy()
        result['target'] = (result[label] > result.groupby('datum')[label].transform('median')).astype(int)
    return result
