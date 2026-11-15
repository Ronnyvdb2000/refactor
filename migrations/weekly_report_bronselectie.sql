ALTER TABLE public.weekly_toppers ADD COLUMN IF NOT EXISTS bronselectie text;
ALTER TABLE public.weekly_toppers ADD CONSTRAINT weekly_toppers_bronselectie_check CHECK (bronselectie IN ('a','x','a,x'));
-- Bestaande historische typen blijven bewaard, zonder een bron te verzinnen.
CREATE UNIQUE INDEX weekly_toppers_report_week_key ON public.weekly_toppers
(ticker,beurs,week_startdatum) WHERE bronselectie IS NOT NULL;
-- De vrijdag vóór week B mag naast de dagwaarneming van week A bestaan.
ALTER TABLE public.weekly_topper_parameters
DROP CONSTRAINT weekly_topper_parameters_ticker_beurs_datum_key;
ALTER TABLE public.weekly_topper_parameters ADD CONSTRAINT weekly_topper_parameters_week_datum_key
UNIQUE (ticker,beurs,week_startdatum,datum);
COMMENT ON COLUMN public.weekly_toppers.bronselectie IS 'Rapportselectie a, x of a,x; NULL voor historische opvolgselecties.';
COMMENT ON COLUMN public.weekly_topper_parameters.dag_index IS '0 = laatste geldige koersdag vóór de week; 1..5 = historische weekdagwaarneming.';
