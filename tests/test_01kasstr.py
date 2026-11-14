"""Offline kasstroom regressions; no external messages or database writes."""
import contextlib
import io
import math
from pathlib import Path
import unittest
from unittest.mock import MagicMock,patch
import pandas as pd
import numpy as np
import bot_01kasstr as b

DATES=pd.to_datetime(['2025-12-31','2024-12-31'])


def statements():
    cf=pd.DataFrame({'Free Cash Flow':[20.,10.], 'Operating Cash Flow':[30.,20.],
        'Capital Expenditure':[-10.,-10.], 'Cash Dividends Paid':[-2.,-1.],
        'Repurchase Of Capital Stock':[-3.,-1.]},index=DATES).T
    bs=pd.DataFrame({'Total Assets':[100.,100.], 'Current Assets':[50.,40.],
        'Current Liabilities':[20.,20.], 'Long Term Debt':[10.,20.],
        'Ordinary Shares Number':[100.,100.]},index=DATES).T
    inc=pd.DataFrame({'Net Income':[20.,10.], 'Total Revenue':[150.,100.],
                      'Gross Profit':[70.,40.]},index=DATES).T
    return cf,bs,inc


def ticker():
    cf,bs,inc=statements()
    tk=MagicMock(cashflow=cf,balance_sheet=bs,financials=inc)
    tk.info=dict(currentPrice=20.123456,regularMarketPrice=19.,marketCap=200.,
        currency='USD',financialCurrency='USD',revenueGrowth=.1,totalDebt=10.,
        totalCash=5.,ebitda=10.,numberOfAnalystOpinions=3,
        regularMarketTime=pd.Timestamp('2026-11-12',tz='UTC').timestamp())
    return tk


class Tests(unittest.TestCase):
    def setUp(self):
        p=patch.object(b,'today_str',return_value='2026-11-14');p.start();self.addCleanup(p.stop)

    def analyse(self,tk=None):
        with patch.object(b.yf,'Ticker',return_value=ticker() if tk is None else tk):
            return b.analyse_ticker('A')

    def test_known_ratios_and_full_scores(self):
        s=self.analyse();self.assertEqual(s.score,8);self.assertEqual(s.piotroski_score,9)
        self.assertEqual(s.fcf_yield,10.);self.assertEqual(s.payout_pct,25.)
        self.assertEqual(s.net_debt_ebitda,.5);self.assertEqual(s.price,20.123456)
        self.assertEqual(s.fiscal_date,'2025-12-31');self.assertEqual(s.first_fiscal_date,'2024-12-31')

    def test_fcf_column_order_does_not_change_result(self):
        tk=ticker();tk.cashflow=tk.cashflow.iloc[:,::-1]
        self.assertEqual(self.analyse(tk).fcf_now,20)
        pd.testing.assert_series_equal(b._fcf_series(tk.cashflow),b._fcf_series(statements()[0]))

    def test_fiscal_dates_preserve_local_timezone(self):
        tk=ticker()
        for frame in (tk.cashflow,tk.balance_sheet,tk.financials):
            frame.columns=frame.columns.tz_localize('Europe/Brussels')
        s=self.analyse(tk);self.assertEqual(s.fiscal_date,'2025-12-31')
        self.assertEqual(s.piotroski_score,9)

    def test_fiscal_duplicate_and_invalid_headers(self):
        cf=statements()[0]
        cf=pd.concat([cf,cf.iloc[:,[0]]],axis=1)
        cf['not-a-date']=99
        normalized=b._annual_statement(cf)
        self.assertEqual(len(normalized.columns),2)
        self.assertEqual(b._fcf_series(normalized).iloc[-1],20)

    def test_fcf_fallback_per_year(self):
        tk=ticker();tk.cashflow.loc['Free Cash Flow',DATES[0]]=np.nan
        self.assertEqual(self.analyse(tk).fcf_now,20)

    def test_fcf_capex_positive_or_negative(self):
        tk=ticker();tk.cashflow=tk.cashflow.drop(index='Free Cash Flow')
        tk.cashflow.loc['Capital Expenditure']=10
        self.assertEqual(self.analyse(tk).fcf_now,20)

    def test_missing_latest_fcf_never_replaced(self):
        tk=ticker();tk.cashflow.loc[['Free Cash Flow','Capital Expenditure'],DATES[0]]=np.nan
        with self.assertRaisesRegex(b.DataUnavailable,'laatste boekjaar'):self.analyse(tk)

    def test_missing_middle_year_not_removed(self):
        cf=statements()[0];cf[pd.Timestamp('2023-12-31')]=5
        cf.loc[['Free Cash Flow','Operating Cash Flow'],DATES[1]]=np.nan
        values=b._fcf_series(cf)
        self.assertEqual(len(values),3);self.assertTrue(math.isnan(values.loc[DATES[1]]))
        tk=ticker();tk.cashflow=cf
        with self.assertRaisesRegex(b.DataUnavailable,'opeenvolgende'):self.analyse(tk)

    def test_older_gap_reported_not_bridge(self):
        tk=ticker();tk.cashflow[pd.Timestamp('2022-12-31')]=1
        s=self.analyse(tk);self.assertEqual(s.fcf_years,2);self.assertIn('afgebroken',s.data_notes)

    def test_gap_between_latest_years_rejected(self):
        tk=ticker();tk.cashflow.columns=pd.to_datetime(['2025-12-31','2023-12-31'])
        with self.assertRaises(b.DataUnavailable):self.analyse(tk)

    def test_stale_or_future_fiscal_rejected(self):
        for dates in [['2027-12-31','2026-12-31'],['2024-03-31','2023-03-31']]:
            tk=ticker();tk.cashflow.columns=pd.to_datetime(dates)
            with self.assertRaises(b.DataUnavailable):self.analyse(tk)

    def test_missing_payout_is_not_zero(self):
        tk=ticker();tk.cashflow=tk.cashflow.drop(index='Repurchase Of Capital Stock')
        s=self.analyse(tk);self.assertTrue(math.isnan(s.payout_pct));self.assertEqual(s.score,7)
        self.assertIn('payout',s.data_notes)

    def test_payout_explicit_zero_known_but_no_point(self):
        tk=ticker();tk.cashflow.loc[['Cash Dividends Paid','Repurchase Of Capital Stock']]=0
        s=self.analyse(tk);self.assertEqual(s.payout_pct,0);self.assertEqual(s.score,7)
        self.assertNotIn('payout (dividend + buybacks) onbekend',s.data_notes)

    def test_payout_same_fiscal_not_column_position(self):
        tk=ticker();tk.cashflow=tk.cashflow.iloc[:,::-1]
        self.assertEqual(self.analyse(tk).payout_pct,25)

    def test_missing_debt_cash_cannot_earn_point(self):
        for field in ['totalDebt','totalCash']:
            tk=ticker();tk.info.pop(field);s=self.analyse(tk)
            self.assertTrue(math.isnan(s.net_debt_ebitda));self.assertEqual(s.score,7)

    def test_explicit_zero_debt_cash_valid(self):
        tk=ticker();tk.info.update(totalDebt=0,totalCash=0)
        self.assertEqual(self.analyse(tk).net_debt_ebitda,0)

    def test_nonfinite_and_negative_debt_inputs(self):
        for field,value in [('totalDebt',np.inf),('totalCash',-1),('ebitda',0),('ebitda',np.inf)]:
            tk=ticker();tk.info[field]=value
            self.assertTrue(math.isnan(self.analyse(tk).net_debt_ebitda))

    def test_nonpositive_fcf_or_ebitda_is_filter_not_missing_data(self):
        tk=ticker();tk.cashflow.loc['Free Cash Flow']=[-1,-2]
        s=self.analyse(tk);self.assertIn('FCF niet positief',s.payout_label)
        self.assertNotIn('payout (dividend + buybacks) onbekend',s.data_notes)
        tk=ticker();tk.info['ebitda']=-1
        s=self.analyse(tk);self.assertIn('EBITDA niet positief',s.debt_label)
        self.assertNotIn('netto schuld/EBITDA onbekend',s.data_notes)

    def test_analyst_negative_fractional_unknown_zero_valid(self):
        for value in [-1,1.5,np.inf]:
            tk=ticker();tk.info['numberOfAnalystOpinions']=value
            self.assertTrue(math.isnan(self.analyse(tk).analisten_count))
        tk=ticker();tk.info['numberOfAnalystOpinions']=0
        self.assertEqual(self.analyse(tk).score,8)

    def test_currency_mismatch_missing_rejected(self):
        for currency in ['EUR',None]:
            tk=ticker();tk.info['financialCurrency']=currency
            with self.assertRaises(b.DataUnavailable):self.analyse(tk)

    def test_subunit_marketcap_uses_base_currency(self):
        tk=ticker();tk.info.update(currency='GBp',financialCurrency='GBP')
        with patch.object(b,'_usd_rate',return_value=1.25) as fx:s=self.analyse(tk)
        self.assertEqual(s.price_currency,'GBp');self.assertEqual(s.market_cap_usd,250)
        self.assertEqual(s.fcf_yield,10);self.assertEqual(fx.call_args.args[0],'GBP')

    def test_usd_cap_threshold(self):
        tk=ticker();tk.info.update(marketCap=4e9,currency='EUR',financialCurrency='EUR')
        with patch.object(b,'_usd_rate',return_value=1.5):s=self.analyse(tk)
        self.assertEqual(s.market_cap_usd,6e9);self.assertTrue(s.marktkap_label.startswith('✗'))

    def test_fx_cache_and_invalid_latest(self):
        cache={}
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.history.return_value=pd.DataFrame({'Close':[1.2]},index=pd.to_datetime(['2026-11-13']))
            self.assertEqual(b._usd_rate('EUR',cache),1.2);b._usd_rate('EUR',cache)
            tk.return_value.history.assert_called_once()
            tk.return_value.history.return_value=pd.DataFrame({'Close':[1.2,np.nan]},index=pd.to_datetime(['2026-11-12','2026-11-13']))
            self.assertTrue(math.isnan(b._usd_rate('GBP',{})))

    def test_missing_fx_unknown_no_point(self):
        with patch.object(b,'_usd_rate',return_value=np.nan):s=self.analyse()
        self.assertEqual(s.score,7);self.assertTrue(math.isnan(s.market_cap_usd))

    def test_invalid_price_fallback(self):
        tk=ticker();tk.info['currentPrice']=np.inf
        self.assertEqual(self.analyse(tk).price,19)
        tk.info['regularMarketPrice']=0
        with self.assertRaises(b.DataUnavailable):self.analyse(tk)

    def test_quote_stale_future_and_unknown(self):
        for date in ['2026-01-01','2026-11-15']:
            tk=ticker();tk.info['regularMarketTime']=pd.Timestamp(date,tz='UTC').timestamp()
            with self.assertRaises(b.DataUnavailable):self.analyse(tk)
        tk=ticker();tk.info.pop('regularMarketTime')
        self.assertIn('versheid niet controleerbaar',self.analyse(tk).data_notes)

    def test_implausible_yield_rejected(self):
        tk=ticker();tk.info['marketCap']=1
        with self.assertRaisesRegex(b.DataUnavailable,'100%'):self.analyse(tk)

    def test_piotroski_aligns_unsorted_statements(self):
        tk=ticker();tk.balance_sheet=tk.balance_sheet.iloc[:,::-1]
        tk.financials=tk.financials.iloc[:,::-1]
        self.assertEqual(self.analyse(tk).piotroski_score,9)

    def test_piotroski_mismatched_year_unknown(self):
        tk=ticker();tk.financials.columns=pd.to_datetime(['2024-12-31','2023-12-31'])
        s=self.analyse(tk);self.assertIsNone(s.piotroski_score)
        self.assertIn('afstemming',s.piotroski_label)

    def test_piotroski_missing_not_false(self):
        tk=ticker();tk.balance_sheet=tk.balance_sheet.drop(index='Long Term Debt')
        s=self.analyse(tk);self.assertIsNone(s.piotroski_score)
        self.assertIsNone(s.piotroski_detail['hefboom_gedaald'])
        self.assertIn('8/8 beschikbare',s.piotroski_label)

    def test_piotroski_zero_assets_undefined(self):
        tk=ticker();tk.balance_sheet.loc['Total Assets',DATES[0]]=0
        s=self.analyse(tk);self.assertIsNone(s.piotroski_score)
        self.assertIsNone(s.piotroski_detail['roa_positief'])

    def test_piotroski_negative_debt_and_zero_shares_unknown(self):
        tk=ticker();tk.balance_sheet.loc['Long Term Debt',DATES[0]]=-1
        tk.balance_sheet.loc['Ordinary Shares Number',DATES[0]]=0
        s=self.analyse(tk);self.assertIsNone(s.piotroski_detail['hefboom_gedaald'])
        self.assertIsNone(s.piotroski_detail['geen_verwatering'])

    def test_missing_piotroski_statements_not_core_failure(self):
        tk=ticker();tk.financials=pd.DataFrame()
        s=self.analyse(tk);self.assertEqual(s.score,8);self.assertIsNone(s.piotroski_score)

    def run_bot(self,db=True,email=True,result='default',error=None,lists=None):
        sig=self.analyse() if result=='default' else result
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt','tickers_042x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A','A'] if lists is None else lists),
            analyse_ticker=MagicMock(return_value=sig,side_effect=error),
            log_selectie=MagicMock(return_value=db),send_email=MagicMock(return_value=email)), \
            patch.object(b.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            failed=None
            try:b.run_live_engine()
            except RuntimeError as exc:failed=exc
            return b.log_selectie,b.send_email,b.analyse_ticker,failed

    def test_live_cache_overlap_db_metadata_telegram_off(self):
        db,email,analyse,error=self.run_bot();self.assertIsNone(error);analyse.assert_called_once()
        self.assertEqual(db.call_count,2);self.assertIn('2/2 selecties',email.call_args.args[1])
        self.assertNotIn('send_telegram',b.__dict__)
        self.assertEqual(db.call_args.kwargs['parameters']['fiscal_date'],'2025-12-31')
        self.assertIsInstance(db.call_args.kwargs['parameters']['piotroski_detail'],dict)

    def test_no_candidates_is_valid(self):
        sig=self.analyse();sig.score=0
        db,email,_,error=self.run_bot(result=sig);self.assertIsNone(error);db.assert_not_called();email.assert_called_once()

    def test_data_problem_emails_and_fails_when_no_success(self):
        _,email,_,error=self.run_bot(error=b.DataUnavailable('geen FCF'))
        self.assertIsNotNone(error);self.assertIn('geen FCF',email.call_args.args[1])
        self.assertIn('[ONVOLLEDIG]',email.call_args.args[0])

    def test_unexpected_analysis_failure(self):self.assertIsNotNone(self.run_bot(error=RuntimeError('bug'))[-1])
    def test_database_false(self):self.assertIsNotNone(self.run_bot(db=False)[-1])
    def test_email_false(self):self.assertIsNotNone(self.run_bot(email=False)[-1])
    def test_missing_lists(self):self.assertIsNotNone(self.run_bot(lists=[])[-1])

    def test_partial_universe_marked_without_false_failure(self):
        sig=self.analyse()
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A','B']),
            analyse_ticker=MagicMock(side_effect=[sig,b.DataUnavailable('ontbrekende FCF')]),
            log_selectie=MagicMock(return_value=True),send_email=MagicMock(return_value=True)), \
            patch.object(b.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            b.run_live_engine()
            self.assertIn('[ONVOLLEDIG]',b.send_email.call_args.args[0])
            self.assertIn('1/2 unieke',b.send_email.call_args.args[1])

    def test_database_exception_reported(self):
        sig=self.analyse()
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A']),analyse_ticker=MagicMock(return_value=sig),
            log_selectie=MagicMock(side_effect=RuntimeError('db test')),send_email=MagicMock(return_value=True)), \
            patch.object(b.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):b.run_live_engine()
            self.assertIn('db test',b.send_email.call_args.args[1])

    def test_ranking_precision_and_ties(self):
        a=self.analyse();c=self.analyse();c.ticker='C';c.piotroski_score=None
        d=self.analyse();d.ticker='B'
        self.assertEqual([s.ticker for s in sorted([c,d,a],key=b.ranking)],['A','B','C'])
        c.piotroski_score=9;c.fcf_yield+=.000001
        self.assertEqual(sorted([c,a],key=b.ranking)[0].ticker,'C')

    def test_report_counts_currency_fiscal_and_model(self):
        sigs=[self.analyse() for _ in range(7)]
        report=b.format_bericht('X',sigs[:5],sigs)
        self.assertIn('7 kandidaten, 5 topselecties',report);self.assertIn('USD 20.1235',report)
        self.assertIn('FCF-boekjaar 2025-12-31',report);self.assertIn('eindbalans',report)

    def test_workflow_services(self):
        import yaml
        doc=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows/run_01kasstr.yml').read_text())
        job=doc['jobs']['bot-01kasstr'];step=job['steps'][-1]
        self.assertEqual(step['run'],'python bot_01kasstr.py live')
        self.assertIn('EMAIL_PASS',step['env']);self.assertIn('SUPABASE_DB_URL',step['env'])
        self.assertNotIn('TELEGRAM_TOKEN',step['env']);self.assertEqual(step['env']['PYTHONUNBUFFERED'],'1')

if __name__=='__main__':unittest.main()
