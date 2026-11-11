#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservatieve kwaliteitscontrole voor nieuwe XGBoost/LightGBM modellen.

Geen database- of GitHub-mutaties. Input is een dict met evaluatiemetrics.
Gebruik alleen op strikt out-of-sample resultaten; vergelijk modellen
uitsluitend als testperiode, target, universum en evaluatiemethode gelijk zijn.
"""
from dataclasses import dataclass, asdict
from math import isfinite
from typing import Optional
import json


@dataclass(frozen=True)
class QualityPolicy:
    min_auc: float = 0.55
    min_excess_return_pp: float = 0.0
    min_test_rows: int = 30
    min_test_dates: int = 5
    max_full_vs_relative_auc_gap: float = 0.10
    min_auc_improvement_vs_incumbent: float = 0.0


def _finite_number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def evaluate_candidate(metrics: dict, incumbent: Optional[dict] = None,
                       policy: QualityPolicy = QualityPolicy()) -> dict:
    """Geeft advies, geen automatische publicatie.

    Verplichte velden: auc, top_model, top_baseline, n_test, n_test_dates,
    auc_relative. Rendementen in dezelfde eenheid (bv. procentpunten).
    incumbent alleen meegeven bij identieke OOS-testset en target.
    """
    issues = []
    auc = _finite_number(metrics.get('auc'))
    top = _finite_number(metrics.get('top_model'))
    baseline = _finite_number(metrics.get('top_baseline'))
    relative = _finite_number(metrics.get('auc_relative'))
    n_test = _finite_number(metrics.get('n_test'))
    n_dates = _finite_number(metrics.get('n_test_dates'))

    if auc is None:
        issues.append('AUC ontbreekt of is ongeldig')
    elif auc < policy.min_auc:
        issues.append(f'AUC {auc:.3f} < {policy.min_auc:.3f}')
    if top is None or baseline is None:
        issues.append('Model- of baselinerendement ontbreekt')
    elif top - baseline <= policy.min_excess_return_pp:
        issues.append(f'Toprendement minus baseline = {top-baseline:+.3f} procentpunt')
    if n_test is None or n_test < policy.min_test_rows:
        issues.append('Te weinig testobservaties')
    if n_dates is None or n_dates < policy.min_test_dates:
        issues.append('Te weinig onafhankelijke testdatums')
    if relative is None:
        issues.append('AUC relatief controlemodel ontbreekt')
    elif auc is not None and auc - relative > policy.max_full_vs_relative_auc_gap:
        issues.append('Mogelijke ticker-fingerprint: grote AUC-kloof relatief model')

    if incumbent is not None:
        if not metrics.get('same_oos_test_set', False):
            issues.append('Geen aantoonbaar identieke OOS-testset voor vergelijking')
        else:
            old_auc = _finite_number(incumbent.get('auc'))
            if old_auc is None or auc is None:
                issues.append('AUC bestaand model ontbreekt')
            elif auc < old_auc + policy.min_auc_improvement_vs_incumbent:
                issues.append(f'Nieuwe AUC {auc:.3f} is niet beter dan bestaande {old_auc:.3f}')

    return {
        'publish_advice': 'HOLD' if issues else 'ELIGIBLE_FOR_REVIEW',
        'issues': issues,
        'metrics': metrics,
        'policy': asdict(policy),
        'note': 'Geen automatische publicatie; controleer ook kalibratie, stabiliteit en leakage.'
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Controleer modelkwaliteit zonder modelbestanden te wijzigen')
    parser.add_argument('metrics_json', help='JSON-bestand met evaluatiemetrics')
    parser.add_argument('--incumbent-json', help='Optioneel: bestaand model op identieke testset')
    args = parser.parse_args()
    with open(args.metrics_json, encoding='utf-8') as f:
        metrics = json.load(f)
    incumbent = None
    if args.incumbent_json:
        with open(args.incumbent_json, encoding='utf-8') as f:
            incumbent = json.load(f)
    result = evaluate_candidate(metrics, incumbent)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result['publish_advice'] == 'ELIGIBLE_FOR_REVIEW' else 2)
