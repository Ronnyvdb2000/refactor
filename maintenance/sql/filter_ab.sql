CREATE TABLE IF NOT EXISTS public.research_filter_ab_cycles (
 cycle_key text PRIMARY KEY, venue text NOT NULL CHECK(venue IN ('048','056','057')),
 mode text NOT NULL CHECK(mode IN ('sample','weekly')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), protocol jsonb NOT NULL,
 source jsonb NOT NULL, status text NOT NULL DEFAULT 'running' CHECK(status IN ('running','complete','partial')),
 result jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS public.research_filter_ab_observations (
 cycle_key text NOT NULL REFERENCES public.research_filter_ab_cycles(cycle_key),
 ticker text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 observation jsonb NOT NULL, observation_hash text NOT NULL CHECK(length(observation_hash)=64),
 PRIMARY KEY(cycle_key,ticker)
);
CREATE TABLE IF NOT EXISTS public.research_filter_ab_notices (
 run_key text PRIMARY KEY, message text NOT NULL,
 status text NOT NULL DEFAULT 'claimed' CHECK(status IN ('claimed','sent','failed','uncertain')),
 message_id bigint, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.research_filter_ab_cycles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_filter_ab_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_filter_ab_notices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.research_filter_ab_cycles,public.research_filter_ab_observations,public.research_filter_ab_notices FROM PUBLIC,anon,authenticated;
CREATE INDEX IF NOT EXISTS research_filter_ab_cycle_state_idx ON public.research_filter_ab_cycles(venue,mode,created_at DESC);
COMMENT ON TABLE public.research_filter_ab_observations IS 'First-observed paired A/B filter decisions, including rejected stocks; never backdate current fundamentals. Not portfolio returns.';
CREATE OR REPLACE FUNCTION public.research_filter_ab_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='research_filter_ab_cycles'
    AND (to_jsonb(NEW)-'status'-'result') IS NOT DISTINCT FROM (to_jsonb(OLD)-'status'-'result') THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'A/B source inputs and observations are immutable';
END $$;
REVOKE ALL ON FUNCTION public.research_filter_ab_guard() FROM PUBLIC,anon,authenticated;
CREATE TRIGGER research_filter_ab_cycles_guard BEFORE UPDATE OR DELETE ON public.research_filter_ab_cycles FOR EACH ROW EXECUTE FUNCTION public.research_filter_ab_guard();
CREATE TRIGGER research_filter_ab_observations_guard BEFORE UPDATE OR DELETE ON public.research_filter_ab_observations FOR EACH ROW EXECUTE FUNCTION public.research_filter_ab_guard();
