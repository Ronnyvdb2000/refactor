-- Private prospective research ledger. No orders; no changes to sel_par or ml_*.
CREATE TABLE public.paper_trials (
 id text PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 protocol jsonb NOT NULL CHECK(jsonb_typeof(protocol)='object')
);
CREATE TABLE public.paper_batches (
 id uuid PRIMARY KEY, trial_id text NOT NULL REFERENCES public.paper_trials(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), snapshot jsonb NOT NULL,
 snapshot_hash text NOT NULL CHECK(length(snapshot_hash)=64),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','open','complete','missing_prices','corporate_action_review')),
 result jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX paper_batches_trial_idx ON public.paper_batches(trial_id,created_at);
CREATE TABLE public.paper_prices (
 batch_id uuid NOT NULL REFERENCES public.paper_batches(id), ticker text NOT NULL, day date NOT NULL,
 open double precision NOT NULL CHECK(open>0 AND open<'Infinity'::float8),
 close double precision NOT NULL CHECK(close>0 AND close<'Infinity'::float8),
 dividend double precision NOT NULL CHECK(dividend>=0 AND dividend<'Infinity'::float8),
 split double precision NOT NULL CHECK(split>=0 AND split<'Infinity'::float8),
 currency text NOT NULL CHECK(currency='USD'), observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(batch_id,ticker,day)
);
CREATE TABLE public.paper_nav (
 batch_id uuid NOT NULL REFERENCES public.paper_batches(id), book text NOT NULL, day date NOT NULL,
 equity double precision NOT NULL CHECK(equity>=0 AND equity<'Infinity'::float8),
 cash double precision NOT NULL CHECK(cash>=-0.0000001 AND cash<'Infinity'::float8),
 invested double precision NOT NULL CHECK(invested>=0 AND invested<'Infinity'::float8),
 positions integer NOT NULL CHECK(positions>=0), PRIMARY KEY(batch_id,book,day)
);
CREATE TABLE public.paper_reports (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 trial_id text NOT NULL REFERENCES public.paper_trials(id),
 checked_at timestamptz NOT NULL DEFAULT clock_timestamp(), report jsonb NOT NULL
);
CREATE INDEX paper_reports_trial_idx ON public.paper_reports(trial_id,checked_at DESC);
ALTER TABLE public.paper_trials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_nav ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_reports ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.paper_trials,public.paper_batches,public.paper_prices,public.paper_nav,public.paper_reports FROM PUBLIC,anon,authenticated;
REVOKE ALL ON SEQUENCE public.paper_reports_id_seq FROM PUBLIC,anon,authenticated;
CREATE FUNCTION public.paper_immutable() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='paper_batches' THEN
  IF (to_jsonb(NEW)-'status'-'result') IS NOT DISTINCT FROM (to_jsonb(OLD)-'status'-'result') THEN
   RETURN NEW;
  END IF;
 END IF;
 RAISE EXCEPTION 'Paper trial protocol, decisions, prices and NAV are append-only';
END $$;
REVOKE ALL ON FUNCTION public.paper_immutable() FROM PUBLIC,anon,authenticated;
CREATE TRIGGER paper_trials_immutable BEFORE UPDATE OR DELETE ON public.paper_trials FOR EACH ROW EXECUTE FUNCTION public.paper_immutable();
CREATE TRIGGER paper_batches_immutable BEFORE UPDATE OR DELETE ON public.paper_batches FOR EACH ROW EXECUTE FUNCTION public.paper_immutable();
CREATE TRIGGER paper_prices_immutable BEFORE UPDATE OR DELETE ON public.paper_prices FOR EACH ROW EXECUTE FUNCTION public.paper_immutable();
CREATE TRIGGER paper_nav_immutable BEFORE UPDATE OR DELETE ON public.paper_nav FOR EACH ROW EXECUTE FUNCTION public.paper_immutable();
CREATE TRIGGER paper_reports_immutable BEFORE UPDATE OR DELETE ON public.paper_reports FOR EACH ROW EXECUTE FUNCTION public.paper_immutable();
COMMENT ON TABLE public.paper_trials IS 'Fixed prospective protocol; started_at excludes all earlier predictions. No claim of profitability.';
COMMENT ON TABLE public.paper_batches IS 'Decision snapshot before next UTC-day US session; only status and derived result can change.';
COMMENT ON TABLE public.paper_prices IS 'First observed Yahoo OHLC and actions. Unverified provider data, not actual executions.';

INSERT INTO public.paper_trials(id,protocol) VALUES('paper_10d_usd_v1','{"benchmark": "SPY", "benchmark_policy": "SPY_same_entry_exit_and_costs_not_continuous_buy_hold", "commission_bps": 5, "currency": "USD", "dividend_policy": "gross_ex_date_cash_proxy_no_tax_or_payment_lag", "engine_hash": "7d64d1f9f95d0e48c4e419897c4864f9a0a99987b05780f1ca7c325af0dbaa6d", "evidence": "descriptive_only_no_automatic_promotion", "execution": "next_session_after_snapshot_utc_date", "fractional_shares": true, "horizon": 10, "implementation_hash": "0f778cb8148674d618c887f38992cd29fa9c99abd912ca280ec118a29d9e835f", "initial_cash": 100000.0, "max_prediction_age_days": 3, "minimum_closed_batches": 12, "model_config": {"embargo_dagen": 25, "fallback_dagen": 90, "ic_max_abs": 0.15, "max_data_leeftijd": 5, "min_datums_ic": 10, "min_ic": 0.02, "rolling_dagen": 28, "top_n": 10}, "models": [{"model": "a_par", "version": "a_par_model_v1"}], "rebalance": "nonoverlapping_10_sessions", "slippage_bps": 10, "split_policy": "block_entire_batch_for_review", "top_n": 10, "version": 1}'::jsonb);
