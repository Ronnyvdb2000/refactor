import ast
from pathlib import Path
import re

from common.sel_par_inputs import sel_par_query, input_columns


def test_all_input_relations_are_served_by_sel_par():
    query=sel_par_query('''SELECT s.ticker,g.rsi14,u.trailing_pe,f.fwd_ret_10d
    FROM public.selecties s JOIN generieke_technicals g USING(ticker,datum)
    LEFT JOIN public.generieke_fundamentals u USING(ticker,datum)
    JOIN forward_returns f USING(ticker,datum,strategie)''')
    assert query.count('FROM public.sel_par')==4
    assert not re.search(r'\b(?:FROM|JOIN)\s+(?:public\.)?(?:selecties|generieke_technicals|generieke_fundamentals|forward_returns)\b',query,re.I)
    assert "fundamentals_tijdig_bekend" in query
    assert "d->'selectie'" in query
    assert query.count("invoer_vrijgegeven'='true'")==4


def test_nested_with_and_bound_parameters_survive():
    source='WITH x AS (SELECT * FROM forward_returns WHERE datum>%s) SELECT * FROM x'
    query=sel_par_query(source)
    assert query.startswith('WITH __sp_returns')
    assert ',\nx AS (' in query
    assert query.count('%s')==1
    assert sel_par_query(query)==query


def test_output_writes_and_metadata_are_not_redirected():
    for query in ['INSERT INTO selecties(ticker) VALUES(%s)',
                  "SELECT * FROM information_schema.columns WHERE table_name='selecties'",
                  'UPDATE xgboost_runs SET status=%s']:
        assert sel_par_query(query)==query


def test_input_schema_is_declared_without_legacy_table_metadata():
    assert 'strategie' in input_columns('forward_returns')
    assert 'rsi14' in input_columns('generieke_technicals')
    assert input_columns('xgboost_scores') is None


def test_every_active_consumer_routes_sql_through_central_input_layer():
    root=Path(__file__).resolve().parents[1]
    for folder in ['models','meta_models','analyses','combiners']:
        for path in (root/folder).glob('*.py'):
            source=path.read_text()
            # Only consumers with a literal legacy input relation need redirection.
            if not re.search(r'\b(?:FROM|JOIN)\s+(?:public\.)?(?:generieke_technicals|generieke_fundamentals|forward_returns)\b',source,re.I):
                continue
            assert 'from common.sel_par_inputs import' in source,path
            for node in ast.walk(ast.parse(source)):
                if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                        and node.func.attr in ('execute','read_sql','read_sql_query') and node.args):
                    argument=node.args[0]
                    assert isinstance(argument,ast.Call) and getattr(argument.func,'id',None)=='sel_par_query',path


def test_old_builder_schedules_are_retired_and_scoring_follows_sel_par():
    import yaml
    root=Path(__file__).resolve().parents[1]
    for name in ['run_generieke_technicals.yml','run_generieke_fundamentals.yml','run_forward_returns.yml']:
        data=yaml.load((root/'.github/workflows'/name).read_text(),Loader=yaml.BaseLoader)
        assert 'schedule' not in data['on']
        assert 'maintenance.retired_feature_builder' in (root/'.github/workflows'/name).read_text()


def test_manifest_accepts_weekend_friday_snapshot_but_rejects_missing_features(monkeypatch):
    from datetime import date
    from unittest.mock import MagicMock
    from maintenance.sel_par_direct import publish_input_manifest,REQUIRED
    import common.feature_pipeline as pipeline
    monkeypatch.setenv('FEATURE_BUILD_MANIFEST','results/feature_build.json')
    captured=[]
    monkeypatch.setattr(pipeline,'write_feature_manifest',lambda rows,ratio:captured.append((rows,ratio)))
    conn=MagicMock()
    good=dict(ticker='A',datum='2026-11-21',bar_datum='2026-11-20',**{'tech_'+k:1. for k in REQUIRED})
    bad={**good,'ticker':'B','tech_rsi14':None}
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value=[good,bad]
    publish_input_manifest(conn,date(2026,11,21))
    rows,ratio=captured[0]
    assert [r['status'] for r in rows]==['geschreven','ontbreekt']
    assert ratio==.80


def test_snapshot_step_and_release_do_not_rewrite_database_function():
    root=Path(__file__).resolve().parents[1]
    source=(root/'maintenance/refresh_sel_par.py').read_text()
    assert source.index('zaai_sel_par')<source.index('snapshot_sql.read_text')
    assert source.index('publish_input_manifest(conn,cutoff)')<source.index('invoer_vrijgegeven')
    snapshot=(root/'maintenance/sql/20261122_sel_par_selection_snapshots.sql').read_text()
    assert 'UPDATE public.sel_par' in snapshot
    assert 'FUNCTION' not in snapshot and 'REVOKE' not in snapshot
