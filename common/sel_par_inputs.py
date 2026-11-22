"""SQL input projections backed exclusively by public.sel_par.

Existing output tables and model contracts stay unchanged. No fallback to legacy
feature stores. Fundamentals fetched after selection are excluded.
"""
import re

SCHEMAS = {
  "selecties": [
    [
      "id",
      "integer"
    ],
    [
      "ticker",
      "text"
    ],
    [
      "datum",
      "text"
    ],
    [
      "strategie",
      "text"
    ],
    [
      "beurs",
      "text"
    ],
    [
      "koers",
      "real"
    ],
    [
      "parameters",
      "jsonb"
    ],
    [
      "created_at",
      "timestamp without time zone"
    ],
    [
      "score",
      "numeric"
    ],
    [
      "total_score",
      "numeric"
    ],
    [
      "grafiek",
      "text"
    ],
    [
      "rsi_monthly",
      "numeric"
    ],
    [
      "rsi_label",
      "text"
    ],
    [
      "macd_label",
      "text"
    ],
    [
      "rr_pct",
      "numeric"
    ],
    [
      "resistance",
      "numeric"
    ],
    [
      "support",
      "numeric"
    ],
    [
      "div_yield",
      "numeric"
    ],
    [
      "atr",
      "numeric"
    ],
    [
      "stop",
      "numeric"
    ],
    [
      "rs",
      "numeric"
    ],
    [
      "pivot",
      "numeric"
    ],
    [
      "pct_from_high",
      "numeric"
    ],
    [
      "pct_from_low",
      "numeric"
    ],
    [
      "n_boxes",
      "integer"
    ],
    [
      "box_top",
      "numeric"
    ],
    [
      "box_bottom",
      "numeric"
    ],
    [
      "box_pct",
      "numeric"
    ],
    [
      "n_contracties",
      "integer"
    ],
    [
      "laatste_pct",
      "numeric"
    ],
    [
      "breakout",
      "boolean"
    ],
    [
      "breakout_vol",
      "numeric"
    ],
    [
      "stage2",
      "boolean"
    ],
    [
      "eps_q_growth_pct",
      "numeric"
    ],
    [
      "eps_annual_cagr_pct",
      "numeric"
    ],
    [
      "inst_pct",
      "numeric"
    ],
    [
      "ma200",
      "numeric"
    ],
    [
      "high52w",
      "numeric"
    ],
    [
      "weekdag_gemiddelde",
      "double precision"
    ],
    [
      "weekdag_winrate",
      "double precision"
    ],
    [
      "weekdag_p",
      "double precision"
    ],
    [
      "maand_gemiddelde",
      "double precision"
    ],
    [
      "maand_winrate",
      "double precision"
    ],
    [
      "maand_p",
      "double precision"
    ],
    [
      "kwartaal_gemiddelde",
      "double precision"
    ],
    [
      "kwartaal_winrate",
      "double precision"
    ],
    [
      "kwartaal_p",
      "double precision"
    ],
    [
      "jaren_data",
      "double precision"
    ],
    [
      "rank",
      "integer"
    ],
    [
      "fcf_yield",
      "double precision"
    ],
    [
      "fcf_years",
      "integer"
    ],
    [
      "fcf_growing",
      "boolean"
    ],
    [
      "fcf_consistent",
      "boolean"
    ],
    [
      "rev_growth_pct",
      "double precision"
    ],
    [
      "net_debt_ebitda",
      "double precision"
    ],
    [
      "payout_pct",
      "double precision"
    ],
    [
      "proba",
      "numeric"
    ],
    [
      "rsi",
      "numeric"
    ],
    [
      "macd_hist",
      "numeric"
    ],
    [
      "macd_hist_slope",
      "numeric"
    ],
    [
      "dist_sma50_pct",
      "numeric"
    ],
    [
      "dist_sma200_pct",
      "numeric"
    ],
    [
      "atr_pct",
      "numeric"
    ],
    [
      "vol_ratio_20d",
      "numeric"
    ],
    [
      "ret_5d",
      "numeric"
    ],
    [
      "ret_20d",
      "numeric"
    ],
    [
      "ret_60d",
      "numeric"
    ],
    [
      "horizon_days",
      "integer"
    ],
    [
      "markt_ret_5d",
      "numeric"
    ],
    [
      "roe_pct",
      "numeric"
    ],
    [
      "terugverdienperiode",
      "numeric"
    ],
    [
      "forward_pe",
      "numeric"
    ],
    [
      "verwachte_winstgroei_pct",
      "numeric"
    ],
    [
      "market_cap",
      "numeric"
    ],
    [
      "analisten_count",
      "integer"
    ],
    [
      "piotroski_score",
      "integer"
    ],
    [
      "current_ratio",
      "double precision"
    ],
    [
      "eps_years",
      "integer"
    ],
    [
      "eps_all_positive",
      "boolean"
    ],
    [
      "eps_growth_pct",
      "double precision"
    ],
    [
      "pe_ratio",
      "double precision"
    ],
    [
      "pb_ratio",
      "double precision"
    ],
    [
      "graham_number_ok",
      "boolean"
    ],
    [
      "roc",
      "double precision"
    ],
    [
      "earnings_yield",
      "double precision"
    ],
    [
      "roc_rank",
      "integer"
    ],
    [
      "ey_rank",
      "integer"
    ],
    [
      "combined_rank",
      "integer"
    ],
    [
      "ebit",
      "double precision"
    ],
    [
      "invested_capital",
      "double precision"
    ],
    [
      "enterprise_value",
      "double precision"
    ],
    [
      "sector",
      "text"
    ],
    [
      "ps_ratio",
      "double precision"
    ],
    [
      "ev_ebitda",
      "double precision"
    ],
    [
      "ev_fcf",
      "double precision"
    ],
    [
      "shareholder_yield",
      "double precision"
    ],
    [
      "vc2_score",
      "double precision"
    ],
    [
      "momentum_6m_pct",
      "double precision"
    ],
    [
      "revenue_growth_pct",
      "double precision"
    ],
    [
      "debt_to_equity",
      "double precision"
    ],
    [
      "pcf_ratio",
      "double precision"
    ],
    [
      "vwap20_pct",
      "double precision"
    ],
    [
      "vwap50_pct",
      "double precision"
    ],
    [
      "insider_net_shares",
      "double precision"
    ],
    [
      "pred_ret_10d",
      "numeric"
    ],
    [
      "pred_ret_30d",
      "numeric"
    ],
    [
      "pred_ret_60d",
      "numeric"
    ],
    [
      "model_versie",
      "text"
    ]
  ],
  "forward_returns": [
    [
      "ticker",
      "text"
    ],
    [
      "datum",
      "text"
    ],
    [
      "strategie",
      "text"
    ],
    [
      "beurs",
      "text"
    ],
    [
      "entry_koers",
      "double precision"
    ],
    [
      "fwd_close_5d",
      "double precision"
    ],
    [
      "fwd_close_10d",
      "double precision"
    ],
    [
      "fwd_close_20d",
      "double precision"
    ],
    [
      "fwd_ret_5d",
      "double precision"
    ],
    [
      "fwd_ret_10d",
      "double precision"
    ],
    [
      "fwd_ret_20d",
      "double precision"
    ],
    [
      "bijgewerkt_op",
      "timestamp without time zone"
    ],
    [
      "fwd_close_30d",
      "double precision"
    ],
    [
      "fwd_ret_30d",
      "double precision"
    ],
    [
      "fwd_close_60d",
      "double precision"
    ],
    [
      "fwd_ret_60d",
      "double precision"
    ]
  ],
  "generieke_technicals": [
    [
      "ticker",
      "text"
    ],
    [
      "datum",
      "text"
    ],
    [
      "atr14",
      "double precision"
    ],
    [
      "atr14_pct",
      "double precision"
    ],
    [
      "rsi14",
      "double precision"
    ],
    [
      "ibs",
      "double precision"
    ],
    [
      "ma50",
      "double precision"
    ],
    [
      "ma200",
      "double precision"
    ],
    [
      "pct_from_ma50",
      "double precision"
    ],
    [
      "pct_from_ma200",
      "double precision"
    ],
    [
      "vol_ratio_20d",
      "double precision"
    ],
    [
      "high52w",
      "double precision"
    ],
    [
      "pct_from_high52w",
      "double precision"
    ],
    [
      "bijgewerkt_op",
      "timestamp without time zone"
    ],
    [
      "macd",
      "double precision"
    ],
    [
      "macd_signaal",
      "double precision"
    ],
    [
      "macd_hist",
      "double precision"
    ],
    [
      "bb_breedte",
      "double precision"
    ],
    [
      "bb_percent_b",
      "double precision"
    ],
    [
      "stoch_k",
      "double precision"
    ],
    [
      "stoch_d",
      "double precision"
    ],
    [
      "adx14",
      "double precision"
    ],
    [
      "rel_sterkte_20d",
      "double precision"
    ],
    [
      "hv20",
      "double precision"
    ],
    [
      "hv60",
      "double precision"
    ],
    [
      "dagen_sinds_low52w",
      "integer"
    ],
    [
      "vol_ratio_50d",
      "double precision"
    ],
    [
      "ema8",
      "double precision"
    ],
    [
      "ema20",
      "double precision"
    ],
    [
      "pct_from_ema8",
      "double precision"
    ],
    [
      "pct_from_ema20",
      "double precision"
    ],
    [
      "ema8_minus_ema20",
      "double precision"
    ]
  ],
  "generieke_fundamentals": [
    [
      "ticker",
      "text"
    ],
    [
      "datum",
      "text"
    ],
    [
      "market_cap",
      "double precision"
    ],
    [
      "trailing_pe",
      "double precision"
    ],
    [
      "price_to_book",
      "double precision"
    ],
    [
      "dividend_yield",
      "double precision"
    ],
    [
      "current_ratio",
      "double precision"
    ],
    [
      "revenue_growth_pct",
      "double precision"
    ],
    [
      "fcf_yield",
      "double precision"
    ],
    [
      "fcf_years",
      "integer"
    ],
    [
      "fcf_growing",
      "boolean"
    ],
    [
      "fcf_consistent",
      "boolean"
    ],
    [
      "net_debt_ebitda",
      "double precision"
    ],
    [
      "payout_pct",
      "double precision"
    ],
    [
      "analisten_count",
      "double precision"
    ],
    [
      "piotroski_score",
      "integer"
    ],
    [
      "eps_growth_pct",
      "double precision"
    ],
    [
      "eps_cagr_pct",
      "double precision"
    ],
    [
      "peg_ratio",
      "double precision"
    ],
    [
      "bijgewerkt_op",
      "timestamp with time zone"
    ]
  ]
}
DEFINITIONS = {
  "selecties": "__sp_selecties AS (\nSELECT r.* FROM public.sel_par p\nCROSS JOIN LATERAL jsonb_array_elements(coalesce(p.strategie_details,'[]'::jsonb)) d\nCROSS JOIN LATERAL jsonb_to_record(\ncoalesce(d->'selectie',to_jsonb(p)||jsonb_build_object('id',d->'bron_id','strategie',d->'strategie','parameters',d->'parameters','score',d->'score','koers',d->'koers'))\n) AS r(\"id\" integer,\n\"ticker\" text,\n\"datum\" text,\n\"strategie\" text,\n\"beurs\" text,\n\"koers\" real,\n\"parameters\" jsonb,\n\"created_at\" timestamp without time zone,\n\"score\" numeric,\n\"total_score\" numeric,\n\"grafiek\" text,\n\"rsi_monthly\" numeric,\n\"rsi_label\" text,\n\"macd_label\" text,\n\"rr_pct\" numeric,\n\"resistance\" numeric,\n\"support\" numeric,\n\"div_yield\" numeric,\n\"atr\" numeric,\n\"stop\" numeric,\n\"rs\" numeric,\n\"pivot\" numeric,\n\"pct_from_high\" numeric,\n\"pct_from_low\" numeric,\n\"n_boxes\" integer,\n\"box_top\" numeric,\n\"box_bottom\" numeric,\n\"box_pct\" numeric,\n\"n_contracties\" integer,\n\"laatste_pct\" numeric,\n\"breakout\" boolean,\n\"breakout_vol\" numeric,\n\"stage2\" boolean,\n\"eps_q_growth_pct\" numeric,\n\"eps_annual_cagr_pct\" numeric,\n\"inst_pct\" numeric,\n\"ma200\" numeric,\n\"high52w\" numeric,\n\"weekdag_gemiddelde\" double precision,\n\"weekdag_winrate\" double precision,\n\"weekdag_p\" double precision,\n\"maand_gemiddelde\" double precision,\n\"maand_winrate\" double precision,\n\"maand_p\" double precision,\n\"kwartaal_gemiddelde\" double precision,\n\"kwartaal_winrate\" double precision,\n\"kwartaal_p\" double precision,\n\"jaren_data\" double precision,\n\"rank\" integer,\n\"fcf_yield\" double precision,\n\"fcf_years\" integer,\n\"fcf_growing\" boolean,\n\"fcf_consistent\" boolean,\n\"rev_growth_pct\" double precision,\n\"net_debt_ebitda\" double precision,\n\"payout_pct\" double precision,\n\"proba\" numeric,\n\"rsi\" numeric,\n\"macd_hist\" numeric,\n\"macd_hist_slope\" numeric,\n\"dist_sma50_pct\" numeric,\n\"dist_sma200_pct\" numeric,\n\"atr_pct\" numeric,\n\"vol_ratio_20d\" numeric,\n\"ret_5d\" numeric,\n\"ret_20d\" numeric,\n\"ret_60d\" numeric,\n\"horizon_days\" integer,\n\"markt_ret_5d\" numeric,\n\"roe_pct\" numeric,\n\"terugverdienperiode\" numeric,\n\"forward_pe\" numeric,\n\"verwachte_winstgroei_pct\" numeric,\n\"market_cap\" numeric,\n\"analisten_count\" integer,\n\"piotroski_score\" integer,\n\"current_ratio\" double precision,\n\"eps_years\" integer,\n\"eps_all_positive\" boolean,\n\"eps_growth_pct\" double precision,\n\"pe_ratio\" double precision,\n\"pb_ratio\" double precision,\n\"graham_number_ok\" boolean,\n\"roc\" double precision,\n\"earnings_yield\" double precision,\n\"roc_rank\" integer,\n\"ey_rank\" integer,\n\"combined_rank\" integer,\n\"ebit\" double precision,\n\"invested_capital\" double precision,\n\"enterprise_value\" double precision,\n\"sector\" text,\n\"ps_ratio\" double precision,\n\"ev_ebitda\" double precision,\n\"ev_fcf\" double precision,\n\"shareholder_yield\" double precision,\n\"vc2_score\" double precision,\n\"momentum_6m_pct\" double precision,\n\"revenue_growth_pct\" double precision,\n\"debt_to_equity\" double precision,\n\"pcf_ratio\" double precision,\n\"vwap20_pct\" double precision,\n\"vwap50_pct\" double precision,\n\"insider_net_shares\" double precision,\n\"pred_ret_10d\" numeric,\n\"pred_ret_30d\" numeric,\n\"pred_ret_60d\" numeric,\n\"model_versie\" text)\nWHERE p.verrijking_status->>'invoer_vrijgegeven'='true'\n)",
  "generieke_technicals": "__sp_technicals AS (\nSELECT DISTINCT ON(p.ticker,coalesce(p.tech_koersdatum::text,p.datum))\np.ticker,\ncoalesce(p.tech_koersdatum::text,p.datum) AS datum,\np.tech_atr14 AS atr14,\np.tech_atr14_pct AS atr14_pct,\np.tech_rsi14 AS rsi14,\np.tech_ibs AS ibs,\np.tech_ma50 AS ma50,\np.tech_ma200 AS ma200,\np.tech_pct_from_ma50 AS pct_from_ma50,\np.tech_pct_from_ma200 AS pct_from_ma200,\np.tech_vol_ratio_20d AS vol_ratio_20d,\np.tech_high52w AS high52w,\np.tech_pct_from_high52w AS pct_from_high52w,\np.tech_bijgewerkt_op AS bijgewerkt_op,\np.tech_macd AS macd,\np.tech_macd_signaal AS macd_signaal,\np.tech_macd_hist AS macd_hist,\np.tech_bb_breedte AS bb_breedte,\np.tech_bb_percent_b AS bb_percent_b,\np.tech_stoch_k AS stoch_k,\np.tech_stoch_d AS stoch_d,\np.tech_adx14 AS adx14,\np.tech_rel_sterkte_20d AS rel_sterkte_20d,\np.tech_hv20 AS hv20,\np.tech_hv60 AS hv60,\np.tech_dagen_sinds_low52w AS dagen_sinds_low52w,\np.tech_vol_ratio_50d AS vol_ratio_50d,\np.tech_ema8 AS ema8,\np.tech_ema20 AS ema20,\np.tech_pct_from_ema8 AS pct_from_ema8,\np.tech_pct_from_ema20 AS pct_from_ema20,\np.tech_ema8_minus_ema20 AS ema8_minus_ema20\nFROM public.sel_par p WHERE p.verrijking_status->>'invoer_vrijgegeven'='true' AND p.technicals_aanwezig\nORDER BY p.ticker,coalesce(p.tech_koersdatum::text,p.datum),p.tech_bijgewerkt_op DESC NULLS LAST,p.datum DESC\n)",
  "generieke_fundamentals": "__sp_fundamentals AS (\nSELECT p.ticker,\np.datum,\np.fund_market_cap AS market_cap,\np.fund_trailing_pe AS trailing_pe,\np.fund_price_to_book AS price_to_book,\np.fund_dividend_yield AS dividend_yield,\np.fund_current_ratio AS current_ratio,\np.fund_revenue_growth_pct AS revenue_growth_pct,\np.fund_fcf_yield AS fcf_yield,\np.fund_fcf_years AS fcf_years,\np.fund_fcf_growing AS fcf_growing,\np.fund_fcf_consistent AS fcf_consistent,\np.fund_net_debt_ebitda AS net_debt_ebitda,\np.fund_payout_pct AS payout_pct,\np.fund_analisten_count AS analisten_count,\np.fund_piotroski_score AS piotroski_score,\np.fund_eps_growth_pct AS eps_growth_pct,\np.fund_eps_cagr_pct AS eps_cagr_pct,\np.fund_peg_ratio AS peg_ratio,\np.fund_bijgewerkt_op AS bijgewerkt_op FROM public.sel_par p\nWHERE p.verrijking_status->>'invoer_vrijgegeven'='true' AND p.fundamentals_aanwezig AND p.fundamentals_tijdig_bekend\n AND (p.fund_observatiedatum IS NULL OR p.fund_observatiedatum<=p.selectiedatum)\n)",
  "forward_returns": "__sp_returns AS (\nSELECT p.ticker,\np.datum,\ns.strategie,\np.beurs,\np.koers_selectiedatum AS entry_koers,\np.koers_5d AS fwd_close_5d,\np.koers_10d AS fwd_close_10d,\np.koers_20d AS fwd_close_20d,\np.rendement_5d_pct AS fwd_ret_5d,\np.rendement_10d_pct AS fwd_ret_10d,\np.rendement_20d_pct AS fwd_ret_20d,\np.forward_bijgewerkt_op AS bijgewerkt_op,\np.koers_30d AS fwd_close_30d,\np.rendement_30d_pct AS fwd_ret_30d,\np.koers_60d AS fwd_close_60d,\np.rendement_60d_pct AS fwd_ret_60d\nFROM public.sel_par p CROSS JOIN LATERAL unnest(p.strategieen) s(strategie)\nWHERE p.verrijking_status->>'invoer_vrijgegeven'='true' AND p.koers_selectiedatum>0 AND p.koers_selectiedatum<'Infinity'::float8\n)"
}
ALIASES = dict(selecties='__sp_selecties',generieke_technicals='__sp_technicals',
               generieke_fundamentals='__sp_fundamentals',forward_returns='__sp_returns')
_SOURCE = re.compile(r'\b(FROM|JOIN)\s+(?:public\.)?('+'|'.join(ALIASES)+r')\b',re.I)

def sel_par_query(query):
    """Redirect read-side source relations and prepend only required projections."""
    if not isinstance(query,str) or not re.match(r'^\s*(SELECT|WITH)\b',query,re.I):
        return query
    if query.lstrip().startswith('WITH __sp_'):
        return query
    used=set()
    def replace(match):
        key=match[2].lower()
        used.add(key)
        return match[1]+' '+ALIASES[key]
    rewritten=_SOURCE.sub(replace,query)
    if not used:
        return query
    prefix=',\n'.join(DEFINITIONS[key] for key in ALIASES if key in used)
    body=rewritten.lstrip()
    if re.match(r'WITH\s+RECURSIVE\b',body,re.I):
        raise ValueError('Recursive input queries require an explicit sel_par projection')
    if re.match(r'WITH\b',body,re.I):
        return 'WITH '+prefix+',\n'+body[4:].lstrip()
    return 'WITH '+prefix+'\n'+body

def input_columns(table):
    """Declared SQL projection columns, independent of legacy table existence."""
    return [name for name,_ in SCHEMAS[table]] if table in SCHEMAS else None

