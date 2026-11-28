"""Central Supabase release contract, stored in sel_par_runs.resultaat.

Artifact provenance alone cannot authorize a consumer. The exact manifest must
still be released in PostgreSQL, under the same lock used by the producer.
"""
from contextlib import contextmanager
from datetime import date, timedelta
import hashlib
import json

MIN_COVERAGE = .80
PARTS = ('selection_seed', 'source_scans', 'feature_enrichment')


def manifest_hash(context):
    return hashlib.sha256(json.dumps(context,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_release(context):
    try:
        if context['schema_version'] != 2 or context['data_ready'] is not True:
            raise ValueError()
        readiness = context['readiness']
        if any(readiness['required_parts'].get(k) is not True for k in PARTS):
            raise ValueError()
        counts = readiness['coverage_by_selection_date']
        if not counts or readiness['latest_selection_date'] != max(counts):
            raise ValueError()
        latest = date.fromisoformat(readiness['latest_selection_date'])
        cutoff = date.fromisoformat(context['selection_cutoff'])
        from .model_inputs import technical_day
        if not technical_day(cutoff).date() <= latest <= cutoff:
            raise ValueError()
        valid_total = 0
        for day, values in counts.items():
            if not cutoff-timedelta(days=3) <= date.fromisoformat(day) <= cutoff:
                raise ValueError()
            total, valid, excluded, pending = (values[k] for k in ('expected_tickers','valid_tickers','excluded_tickers','pending_tickers'))
            if any(type(v) is not int or v < 0 for v in (total,valid,excluded,pending)):
                raise ValueError()
            if not total or total != valid+excluded+pending or pending:
                raise ValueError()
            valid_total += valid
        values = counts[readiness['latest_selection_date']]
        ratio = readiness['minimum_coverage']
        if type(ratio) not in (float,int) or not MIN_COVERAGE <= ratio <= 1:
            raise ValueError()
        if values['valid_tickers']/values['expected_tickers'] < ratio:
            raise ValueError()
        if context['rows_written'] != valid_total:
            raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError,ZeroDivisionError):
        raise ValueError('Supabase-vrijgave mist complete onderdelen, geldige peildatum of minimale tickerdekking') from None
    return context


def sources_ready(report):
    return isinstance(report,dict) and all(isinstance(report.get(k),list) and not report[k]
        for k in ('missing','active','failed_sources'))


def assert_ready(conn, context):
    """Caller must hold shared/exclusive advisory lock (741206,11)."""
    validate_release(context)
    with conn.cursor() as cur:
        cur.execute('SELECT resultaat FROM public.sel_par_runs WHERE selecties_tot=%s',
                    (context['selection_cutoff'],))
        row = cur.fetchone()
    result = row[0] if row else {}
    return validate_stored(result,context)


def validate_stored(result, context):
    validate_release(context)
    sources=result.get('bronruns')
    if (not sources_ready(sources)
            or result.get('input_released') is not True or result.get('data_ready') is not True
            or result.get('enrichment',{}).get('complete_execution') is not True
            or result.get('run_manifest') != context
            or result.get('manifest_hash') != manifest_hash(context)):
        raise RuntimeError('Exacte bronrun is niet (meer) vrijgegeven in Supabase; geen vervolgstap gestart')
    return result


@contextmanager
def ready_input(conn, context):
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock_shared(741206,11)')
            locked = cur.fetchone()[0]
        if not locked:
            raise RuntimeError('sel_par wordt bijgewerkt; geen vervolgstap gestart')
        # Release MVCC snapshot/transaction, while retaining the session lock.
        conn.commit()
        assert_ready(conn,context)
        conn.commit()
        yield
    finally:
        conn.rollback()
        if locked:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock_shared(741206,11)')
            conn.commit()
