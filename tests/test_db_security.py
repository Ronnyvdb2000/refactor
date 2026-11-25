"""Tabellen die door code in `public` worden aangemaakt moeten afgeschermd zijn."""
import re

import pytest

from common.db_security import lockdown_sql
from common.sel_par_inputs import sel_par_query
from common import xgboost_evaluation
from research import journal

TABELLEN = [
    (journal.DDL, 'public.research_basket_log'),
    (xgboost_evaluation.DDL, 'public.xgboost_prediction_log'),
]


def test_lockdown_sql_enables_rls_and_revokes_anon_and_authenticated():
    sql = lockdown_sql('public.demo')
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "ARRAY['anon', 'authenticated']" in sql
    assert "REVOKE ALL ON public.demo FROM" in sql
    assert "pg_roles" in sql  # overslaan als de rollen niet bestaan (lokaal, CI)


def test_lockdown_sql_has_no_percent_signs_so_it_survives_parameterised_execute():
    assert '%' not in lockdown_sql('public.demo')


@pytest.mark.parametrize('ddl,tabel', TABELLEN)
def test_created_tables_are_locked_down_in_the_same_script(ddl, tabel):
    assert ddl.startswith(f'CREATE TABLE IF NOT EXISTS {tabel} (')
    assert ddl.endswith(lockdown_sql(tabel))


@pytest.mark.parametrize('ddl,tabel', TABELLEN)
def test_ddl_is_not_rewritten_by_the_central_input_layer(ddl, tabel):
    assert sel_par_query(ddl) == ddl


def test_every_runtime_create_table_in_public_has_a_lockdown():
    """Nieuwe `CREATE TABLE ... public.` in Python-code zonder lockdown_sql faalt hier."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    zonder = []
    for pad in list(root.glob('common/*.py')) + list(root.glob('research/*.py')) + \
            list(root.glob('models/*.py')) + list(root.glob('meta_models/*.py')):
        tekst = pad.read_text(encoding='utf-8')
        if re.search(r'CREATE TABLE IF NOT EXISTS public\.', tekst) and 'lockdown_sql' not in tekst:
            zonder.append(pad.name)
    assert not zonder, f'CREATE TABLE zonder lockdown_sql: {zonder}'
