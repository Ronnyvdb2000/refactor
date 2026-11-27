CREATE TABLE public.research_backtests (
 run_key text PRIMARY KEY, source_sha text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), config jsonb NOT NULL,
 selection_snapshot jsonb NOT NULL,
 status text NOT NULL DEFAULT 'running',result jsonb NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE public.research_backtests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.research_backtests FROM PUBLIC,anon,authenticated;
COMMENT ON TABLE public.research_backtests IS 'Exploratory historical reconstructions only. No prospective predictions or actual paper-trial results. Historical features may be backfilled.';

CREATE TABLE public.research_backtest_cache (
 run_key text NOT NULL REFERENCES public.research_backtests(run_key),
 ticker text NOT NULL, data jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(run_key,ticker)
);
ALTER TABLE public.research_backtest_cache ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.research_backtest_cache FROM PUBLIC,anon,authenticated;
COMMENT ON TABLE public.research_backtest_cache IS 'Historical adjusted training/execution research proxies cached per ticker; not a live price ledger.';
