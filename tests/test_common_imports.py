"""Compatibility of the public API and provider-free helper imports."""
import subprocess
import sys
from pathlib import Path
import importlib

ROOT = Path(__file__).resolve().parents[1]


def isolated(source):
    result = subprocess.run([sys.executable, '-c', source], cwd=ROOT, capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_light_helpers_import_without_scientific_or_provider_dependencies():
    isolated('''
import sys
for name in ('numpy','pandas','yfinance','requests','psycopg2','scipy','lightgbm'):
    sys.modules[name] = None
import common
assert 'download_history' in dir(common)
from common.horizons import calendar_embargo
from common.fundamentals import KNOWN_FUNDAMENTALS_SQL
from common.reports import write_json
from common.exports import write_csv
from common import safe_float, trade_cost, download_workers
assert calendar_embargo('60d') == 86
assert safe_float('3') == 3
assert trade_cost(1000) == 15
assert callable(write_json) and callable(write_csv)
for name in ('common.io','common.indicators','common.notify'):
    assert name not in sys.modules
try:
    common.not_an_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown export must fail')
''')


def test_public_exports_resolve_to_original_objects():
    import common
    for name, (module, attribute) in common._EXPORTS.items():
        assert getattr(common, name) is getattr(importlib.import_module('common.' + module), attribute)
    assert set(common.__all__) == set(common._EXPORTS)


def test_import_star_and_submodule_imports_remain_supported():
    isolated('''
from common import *
from common import io, notify
from common.io import download_history as original
assert download_history is original
assert send_telegram is notify.send_telegram
assert io.download_history is download_history
''')
