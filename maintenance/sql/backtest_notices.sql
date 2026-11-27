CREATE TABLE IF NOT EXISTS public.research_backtest_notices (
 run_key text NOT NULL REFERENCES public.research_backtests(run_key),
 message_hash text NOT NULL CHECK(length(message_hash)=64), message text NOT NULL,
 status text NOT NULL DEFAULT 'claimed' CHECK(status IN ('claimed','sent','failed','uncertain')),
 message_id bigint, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(run_key,message_hash)
);
ALTER TABLE public.research_backtest_notices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.research_backtest_notices FROM PUBLIC,anon,authenticated;
COMMENT ON TABLE public.research_backtest_notices IS 'Historical backtest Telegram outbox; claim before HTTP, no automatic retry of uncertain delivery.';
