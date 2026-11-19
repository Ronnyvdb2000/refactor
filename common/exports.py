"""Optional CSV reports; persistence and inter-workflow inputs use their own paths."""
import os


def csv_exports_enabled():
    return os.environ.get('CSV_EXPORT_ENABLED', '0').strip().lower() in {'1', 'true', 'yes'}


def write_csv(frame, path, **kwargs):
    if csv_exports_enabled():
        frame.to_csv(path, **kwargs)
    else:
        print(f'[CSV uitgeschakeld] {path}', flush=True)
