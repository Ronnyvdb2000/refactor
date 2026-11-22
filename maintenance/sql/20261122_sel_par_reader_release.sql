-- One-time release of the existing, verified migration snapshot.
-- The daily pipeline releases subsequent batches only after enrichment/manifest validation.
UPDATE public.sel_par SET verrijking_status=verrijking_status ||
 '{"invoer_vrijgegeven":true}'::jsonb
WHERE NOT(verrijking_status ? 'invoer_vrijgegeven');
