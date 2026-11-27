-- Operational delivery ledger; frozen paper protocol, snapshots and pricing unchanged.
CREATE TABLE public.paper_notices (
 trial_id text NOT NULL REFERENCES public.paper_trials(id),
 event_key text NOT NULL,
 batch_id uuid NOT NULL REFERENCES public.paper_batches(id),
 message text NOT NULL, message_hash text NOT NULL CHECK(length(message_hash)=64),
 status text NOT NULL DEFAULT 'claimed' CHECK(status IN ('claimed','sent','failed','uncertain')),
 message_id bigint, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(trial_id,event_key)
);
ALTER TABLE public.paper_notices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.paper_notices FROM PUBLIC,anon,authenticated;
COMMENT ON TABLE public.paper_notices IS 'Telegram operational outbox: claim before HTTP; confirmed rejection may retry, uncertain delivery requires manual review. No trading changes.';
