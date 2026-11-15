"""Opslag van a/x-topvijf >10%, met één snapshot vóór de stijgingsweek."""
from datetime import timedelta
import pandas as pd
from . import weekly_db_opvolg as db
from .shared import WeekKoersen
from .weekly_report_opvolg import haal_technische_parameters


def bewaar_selecties(entries, maandag, vrijdag):
    if not entries:
        print('[weekly] Geen topvijf-stijgers boven 10%; geen extra historie/opslag.')
        return
    if any(e['week_perf'] <= 10 or e['type'] != 'stijger' for e in entries):
        raise ValueError('Alleen geselecteerde stijgers boven 10%')
    # Bewaar ook de selectie als de extra historie faalt. Een herhaalrun vult aan.
    db.log_report_toppers([dict(e, week_startdatum=maandag) for e in entries])
    cutoff = maandag - timedelta(days=1)
    historie = WeekKoersen(maandag, cutoff, historie=True)
    historie.laad(sorted({e['ticker'] for e in entries}))
    parameters, ontbrekend = [], []
    for ticker, beurs in sorted({(e['ticker'], e['beurs']) for e in entries}):
        hist = historie.frames.get(ticker, pd.DataFrame())
        if hist.empty:
            ontbrekend.append(ticker)
            continue
        hist = hist.loc[hist.index < pd.Timestamp(maandag)]
        if hist.empty or (pd.Timestamp(maandag)-hist.index[-1]).days > 7:
            ontbrekend.append(ticker)
            continue
        peildatum = hist.index[-1].date()
        rows = haal_technische_parameters(hist, peildatum, peildatum)
        if not rows:
            ontbrekend.append(ticker)
            continue
        parameters.append(dict(rows[-1], week_startdatum=maandag, ticker=ticker,
                               beurs=beurs, dag_index=0))
    groepen = {}
    for row in parameters:
        groepen.setdefault((row['beurs'], row['datum']), []).append(row)
    for (beurs, datum), rows in groepen.items():
        ranks = db.haal_laatste_rank_scores([r['ticker'] for r in rows], datum, beurs)
        for row in rows:
            row.update(ranks.get(row['ticker'], {}))
    db.log_weekly_topper_parameters(parameters)
    print(f'[weekly] {len(entries)} selecties >10%; {len(parameters)} snapshots vóór de week verwerkt.')
    if ontbrekend:
        raise RuntimeError('Onvoldoende historische parameters voor: '+', '.join(ontbrekend))
