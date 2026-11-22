-- Keep all strategy-specific input parameters inside the single ticker/day row.
WITH snapshots AS (
 SELECT p.ticker,p.datum,
 jsonb_agg(jsonb_build_object('strategie',s.strategie,'bron_id',s.id,
 'parameters',s.parameters,'score',s.score,'koers',s.koers,'selectie',to_jsonb(s))
 ORDER BY s.strategie) details
 FROM public.sel_par p
 CROSS JOIN LATERAL unnest(p.strategie_bron_ids) b(id)
 JOIN public.selecties s ON s.id=b.id
 GROUP BY p.ticker,p.datum
)
UPDATE public.sel_par p SET strategie_details=x.details
FROM snapshots x WHERE x.ticker=p.ticker AND x.datum=p.datum;
