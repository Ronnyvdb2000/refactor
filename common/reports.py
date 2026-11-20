"""Tabular JSON reports without importing provider or notification packages."""

import os


def csv_exports_enabled():
    return os.environ.get('CSV_EXPORT_ENABLED', '0').strip().lower() in {'1', 'true', 'yes'}


def write_csv(frame, path, **kwargs):
    if csv_exports_enabled():
        frame.to_csv(path, **kwargs)
    else:
        print(f'[CSV uitgeschakeld] {path}', flush=True)

def write_json(frame, path):
    """Write tabular report records with ISO dates and JSON null for missing data."""
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_json(path, orient='records', date_format='iso', force_ascii=False, indent=2, double_precision=15)
