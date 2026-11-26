-- Permanent model audit trail. No public API access; existing tables unchanged.
CREATE TABLE public.ml_runs (
 id uuid PRIMARY KEY, run_key text NOT NULL UNIQUE,
 model text NOT NULL, version text NOT NULL, horizon integer NOT NULL CHECK(horizon IN (5,10,20,30,60)),
 mode text NOT NULL CHECK(mode IN ('prospective','backtest','legacy')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), source_sha text,
 source_run text, source_attempt integer, model_hash text,
 train_start date, train_end date, target text NOT NULL,
 top_n integer NOT NULL CHECK(top_n>0), cohort_hash text NOT NULL,
 config jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE public.ml_params (
 run_id uuid NOT NULL REFERENCES public.ml_runs(id), parameter text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('signed_ic','importance','coefficient')), selected boolean NOT NULL,
 weight double precision, ic double precision, ic_raw double precision, ir double precision,
 n_dates integer, n_obs integer, PRIMARY KEY(run_id,parameter)
);
CREATE TABLE public.ml_scores (
 run_id uuid NOT NULL REFERENCES public.ml_runs(id), datum date NOT NULL, ticker text NOT NULL,
 score double precision NOT NULL CHECK(score NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
 rank integer NOT NULL CHECK(rank>0), picked boolean NOT NULL, entry_close double precision,
 PRIMARY KEY(run_id,datum,ticker), UNIQUE(run_id,datum,rank)
);
CREATE INDEX ml_scores_date_idx ON public.ml_scores(datum,ticker);
CREATE TABLE public.ml_outcomes (
 run_id uuid NOT NULL, datum date NOT NULL, ticker text NOT NULL,
 basis text NOT NULL CHECK(basis IN ('selection_close','next_open')),
 status text NOT NULL, entry_date date, exit_date date, entry_price double precision, exit_price double precision,
 gross_pct double precision, checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,datum,ticker,basis),
 FOREIGN KEY(run_id,datum,ticker) REFERENCES public.ml_scores(run_id,datum,ticker)
);
CREATE TABLE public.ml_eval (
 run_id uuid NOT NULL REFERENCES public.ml_runs(id), datum date NOT NULL, basis text NOT NULL,
 cost_bps double precision NOT NULL CHECK(cost_bps>=0), status text NOT NULL,
 n_scores integer NOT NULL, n_mature integer NOT NULL, n_top integer NOT NULL,
 top_gross_pct double precision, top_net_pct double precision, win_rate double precision,
 baseline_pct double precision, excess_pp double precision, rank_ic double precision,
 checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,datum,basis,cost_bps)
);
CREATE TABLE public.ml_compare (
 run_a uuid NOT NULL REFERENCES public.ml_runs(id), run_b uuid NOT NULL REFERENCES public.ml_runs(id),
 datum date NOT NULL, basis text NOT NULL, cost_bps double precision NOT NULL,
 status text NOT NULL, n_common integer NOT NULL, n_mature integer NOT NULL,
 top_a_pct double precision, top_b_pct double precision, difference_pp double precision,
 baseline_pct double precision, ic_a double precision, ic_b double precision,
 checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_a,run_b,datum,basis,cost_bps), CHECK(run_a<>run_b)
);
ALTER TABLE public.ml_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ml_params ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ml_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ml_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ml_eval ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ml_compare ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ml_runs,public.ml_params,public.ml_scores,public.ml_outcomes,public.ml_eval,public.ml_compare FROM PUBLIC,anon,authenticated;
