#!/usr/bin/env python3
"""Maak een nieuwe xgboostV3.py met conservatieve kwaliteitsgate.

Gebruik: python patch_xgboost_quality.py xgboostV3.py --output xgboostV3_gecontroleerd.py
Het origineel wordt nooit overschreven. Zet model_quality_gate.py naast de output.
"""
import argparse
from pathlib import Path

OLD = '''    try:

        sla_model_veilig_op(
            model,
            horizon,
            FEATURE_COLUMNS,
        )

    except Exception as e:'''
NEW = '''    # Kwaliteitsgate: nooit een bestaand model overschrijven bij HOLD.
    # Dit is een absolute drempel, GEEN vergelijking met het vorige model:
    # die vereist dezelfde out-of-sample testperiode.
    from model_quality_gate import evaluate_candidate

    kwaliteitsadvies = evaluate_candidate({
        "auc": auc,
        "top_model": top_model,
        "top_baseline": top_baseline,
        "n_test": len(test_df),
        "n_test_dates": int(test_df["datum"].nunique()),
        "auc_relative": auc_relatief,
    })
    publiceer = kwaliteitsadvies["publish_advice"] == "ELIGIBLE_FOR_REVIEW"
    if not publiceer:
        print(f"[{horizon}] MODEL NIET GEPUBLICEERD: "
              + "; ".join(kwaliteitsadvies["issues"]))
        print(f"[{horizon}] Bestaand modelbestand blijft ongemoeid.")
    else:
        print(f"[{horizon}] Kwaliteitsgate geslaagd; kandidaat wordt opgeslagen.")

    try:
        if publiceer:
            sla_model_veilig_op(
                model,
                horizon,
                FEATURE_COLUMNS,
            )

    except Exception as e:'''


def patch(source):
    if 'kwaliteitsadvies = evaluate_candidate({' in source:
        raise ValueError('Kwaliteitsgate lijkt al aanwezig; niet dubbel toepassen')
    if source.count(OLD) != 1:
        raise ValueError('Verwachte opslagcode niet exact één keer gevonden; bronversie controleren')
    result = source.replace(OLD, NEW)
    # Houd status semantisch correct: HOLD is geen gepubliceerd model.
    anchor = '''    return {
        "horizon": horizon,
        "status": "getraind",
        "opmerking": None,'''
    replacement = '''    return {
        "horizon": horizon,
        "status": "getraind" if publiceer else "afgekeurd",
        "opmerking": None if publiceer else "; ".join(kwaliteitsadvies["issues"]),'''
    if result.count(anchor) != 1:
        raise ValueError('Verwachte resultaatstructuur niet gevonden')
    return result.replace(anchor, replacement)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, default=Path('xgboostV3_gecontroleerd.py'))
    args = ap.parse_args()
    if args.source.resolve() == args.output.resolve():
        ap.error('Output mag niet hetzelfde zijn als de bron')
    result = patch(args.source.read_text(encoding='utf-8'))
    compile(result, str(args.output), 'exec')
    args.output.write_text(result, encoding='utf-8')
    print(f'Veilig gegenereerd: {args.output}')


if __name__ == '__main__':
    main()
