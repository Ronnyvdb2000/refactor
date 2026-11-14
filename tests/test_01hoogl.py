"""Offline Hoogland regressions: no delivery or database mutations."""
import contextlib
import io
import math
from pathlib import Path
import unittest
from unittest.mock import MagicMock,patch
import pandas as pd
import bot_01hoogl as b


def info():
    return dict(currentPrice=20.123456,regularMarketPrice=19,currency='USD',financialCurrency='USD',
        regularMarketTime=pd.Timestamp('2026-11-12',tz='UTC').timestamp(),bookValue=10,
        trailingEps=2,forwardEps=2.4,returnOnEquity=.2,forwardPE=10,marketCap=1e9,
        numberOfAnalystOpinions=3,dividendYield=2)


class Tests(unittest.TestCase):
    def setUp(self):
        p=patch.object(b,'today_str',return_value='2026-11-13');p.start();self.addCleanup(p.stop)

    def analyse(self,values=None,mode='live'):
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.info=info() if values is None else values
            return b.analyse_ticker('A',b.MODUS_CFG[mode])[0]

    def test_known_six_scores_precision(self):
        s=self.analyse();self.assertEqual(s.score,6);self.assertEqual(s.price,20.123456)
        self.assertAlmostEqual(s.terugverdienperiode,5.061728)
        self.assertAlmostEqual(s.verwachte_winstgroei_pct,20)
        self.assertEqual(s.quote_date,'2026-11-12')

    def test_full_thresholds(self):
        v=info();v['returnOnEquity']=.16
        self.assertEqual(self.analyse(v).score,6);self.assertEqual(self.analyse(v,'full').score,5)

    def test_bad_price_falls_back(self):
        for value in [0,-1,float('nan'),float('inf')]:
            v=info();v['currentPrice']=value
            self.assertEqual(self.analyse(v).price,19)

    def test_both_prices_bad(self):
        v=info();v.update(currentPrice=float('inf'),regularMarketPrice=0)
        self.assertIsNone(self.analyse(v))

    def test_missing_currency(self):
        v=info();v.pop('currency');self.assertIsNone(self.analyse(v))

    def test_stale_and_future_quotes(self):
        for date in ['2026-10-01','2026-11-14']:
            v=info();v['regularMarketTime']=pd.Timestamp(date,tz='UTC').timestamp()
            self.assertIsNone(self.analyse(v))

    def test_unknown_quote_date_disclosed(self):
        v=info();v.pop('regularMarketTime');s=self.analyse(v)
        self.assertIn('versheid niet controleerbaar',s.data_notes)

    def test_missing_fundamentals_stay_unknown(self):
        v=info()
        for name in ['returnOnEquity','forwardPE','trailingEps','forwardEps','marketCap','numberOfAnalystOpinions','dividendYield']:v.pop(name)
        s=self.analyse(v);self.assertEqual(s.score,0)
        for value in [s.roe_pct,s.forward_pe,s.terugverdienperiode,s.verwachte_winstgroei_pct,s.market_cap_usd,s.analisten_count,s.div_yield]:self.assertTrue(math.isnan(value))

    def test_nonfinite_fundamentals_cannot_score(self):
        v=info()
        for name in ['returnOnEquity','forwardPE','trailingEps','forwardEps','marketCap','numberOfAnalystOpinions']:v[name]=float('inf')
        self.assertEqual(self.analyse(v).score,0)

    def test_negative_and_fractional_analysts_rejected(self):
        for value in [-1,1.5]:
            v=info();v['numberOfAnalystOpinions']=value
            self.assertEqual(self.analyse(v).score,5)
            self.assertTrue(math.isnan(self.analyse(v).analisten_count))

    def test_explicit_zero_analysts_valid(self):
        v=info();v['numberOfAnalystOpinions']=0;self.assertEqual(self.analyse(v).score,6)

    def test_nonpositive_earnings_unknown_payback_growth(self):
        for value in [0,-2]:
            v=info();v['trailingEps']=value;s=self.analyse(v)
            self.assertTrue(math.isnan(s.terugverdienperiode));self.assertTrue(math.isnan(s.verwachte_winstgroei_pct))

    def test_negative_payback_clamped_to_zero(self):
        v=info();v['bookValue']=30;self.assertEqual(self.analyse(v).terugverdienperiode,0)

    def test_currency_mismatch_disables_payback_but_not_relative_growth(self):
        v=info();v['financialCurrency']='EUR';s=self.analyse(v)
        self.assertTrue(math.isnan(s.terugverdienperiode));self.assertAlmostEqual(s.verwachte_winstgroei_pct,20)

    def test_subunits_not_mixed_with_book_eps(self):
        v=info();v.update(currency='GBp',financialCurrency='GBP',currentPrice=2000)
        with patch.object(b,'usd_rate',return_value=1.25):s=self.analyse(v)
        self.assertTrue(math.isnan(s.terugverdienperiode));self.assertEqual(s.price_currency,'GBp')
        self.assertEqual(s.market_cap_usd,1.25e9)

    def test_marketcap_usd_threshold(self):
        v=info();v.update(currency='EUR',financialCurrency='EUR',marketCap=4e9)
        with patch.object(b,'usd_rate',return_value=1.5):s=self.analyse(v)
        self.assertEqual(s.score,5);self.assertEqual(s.market_cap_usd,6e9)

    def test_missing_fx_no_point(self):
        v=info();v.update(currency='EUR',financialCurrency='EUR')
        with patch.object(b,'usd_rate',return_value=float('nan')):s=self.analyse(v)
        self.assertEqual(s.score,5);self.assertIn('FX ontbreekt',s.data_notes)

    def test_fx_cache_and_staleness(self):
        cache={}
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.history.return_value=pd.DataFrame({'Close':[1.2]},index=pd.to_datetime(['2026-11-12']))
            self.assertEqual(b.usd_rate('EUR',cache),1.2);self.assertEqual(b.usd_rate('EUR',cache),1.2)
            tk.return_value.history.assert_called_once()
            tk.return_value.history.return_value=pd.DataFrame({'Close':[1.2]},index=pd.to_datetime(['2026-10-01']))
            self.assertTrue(math.isnan(b.usd_rate('GBP',{})))

    def test_fx_invalid_latest_not_replaced(self):
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.history.return_value=pd.DataFrame({'Close':[1.2,float('nan')]},index=pd.to_datetime(['2026-11-11','2026-11-12']))
            self.assertTrue(math.isnan(b.usd_rate('EUR',{})))

    def test_rate_limit_detection_and_propagation(self):
        for text in ['429','Too Many Requests','Rate limited']:
            self.assertTrue(b.is_rate_limit_fout(RuntimeError(text)))
        with patch.object(b.yf,'Ticker',side_effect=RuntimeError('429')):
            self.assertEqual(b.analyse_ticker('A',b.MODUS_CFG['live']),(None,True))

    def test_unexpected_error_not_silently_data(self):
        with patch.object(b.yf,'Ticker',side_effect=RuntimeError('code bug')):
            with self.assertRaises(RuntimeError):b.analyse_ticker('A',b.MODUS_CFG['live'])

    def run_bot(self,db=True,email=True,result='default',mode='live'):
        sig=self.analyse() if result=='default' else result
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt','tickers_042x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A','A']),analyse_ticker=MagicMock(return_value=(sig,False)),
            log_selectie=MagicMock(return_value=db),send_email=MagicMock(return_value=email)), \
            patch.object(b.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            failed=None
            try:b.run_engine(mode)
            except RuntimeError as exc:failed=exc
            return b.log_selectie,b.send_email,b.analyse_ticker,failed

    def test_cache_overlap_services_telegram_off(self):
        db,email,analyse,error=self.run_bot();self.assertIsNone(error)
        self.assertEqual(db.call_count,2);analyse.assert_called_once()
        self.assertNotIn('send_telegram',b.__dict__)
        self.assertIn('2/2 selecties',email.call_args.args[1])
        self.assertEqual(db.call_args.kwargs['parameters']['price_currency'],'USD')

    def test_empty_selection_is_valid(self):
        sig=self.analyse();sig.score=0
        db,email,_,error=self.run_bot(result=sig);self.assertIsNone(error)
        db.assert_not_called();email.assert_called_once()

    def test_no_data_fails_and_emails(self):
        _,email,_,error=self.run_bot(result=None);self.assertIsNotNone(error)
        self.assertIn('[ONVOLLEDIG]',email.call_args.args[0])

    def test_database_failure(self):self.assertIsNotNone(self.run_bot(db=False)[-1])
    def test_email_failure(self):self.assertIsNotNone(self.run_bot(email=False)[-1])

    def test_rate_limit_stop_visible_and_fails(self):
        with patch.multiple(b,RATE_LIMIT_CONSECUTIVE_DREMPEL=1,RATE_LIMIT_MAX_PAUZES=0,
            bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A','B']),analyse_ticker=MagicMock(return_value=(None,True)),
            send_email=MagicMock(return_value=True)),patch.object(b.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):b.run_engine('live')
            self.assertIn('Niet verwerkt na stop: B',b.send_email.call_args.args[1])

    def test_ranking_unknown_last_ties_ticker(self):
        a=self.analyse();c=self.analyse();c.ticker='C';c.terugverdienperiode=float('nan')
        d=self.analyse();d.ticker='B'
        self.assertEqual([s.ticker for s in sorted([c,d,a],key=b.ranking)],['A','B','C'])

    def test_report_currency_candidate_count(self):
        sigs=[self.analyse() for _ in range(7)]
        report=b.format_bericht('X',sigs[:5],sigs,b.MODUS_CFG['live'])
        self.assertIn('7 kandidaten, 5 topselecties',report);self.assertIn('USD 20.1235',report)
        self.assertNotIn('EUR20',report)

    def test_full_mode_strategy(self):
        db,_,_,error=self.run_bot(mode='full');self.assertIsNone(error)
        self.assertEqual(db.call_args.kwargs['strategie'],'bot_01hoogl_full')

    def test_workflow_services_and_modes(self):
        import yaml
        for filename,mode in [('run_01hoogl.yml','live'),('run_01hoogl_full.yml','full')]:
            doc=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows'/filename).read_text())
            job=next(iter(doc['jobs'].values()));step=job['steps'][-1]
            self.assertEqual(step['run'],f'python bot_01hoogl.py {mode}')
            self.assertIn('SUPABASE_DB_URL',step['env']);self.assertIn('EMAIL_PASS',step['env'])
            self.assertNotIn('TELEGRAM_TOKEN',step['env']);self.assertEqual(step['env']['PYTHONUNBUFFERED'],'1')

if __name__=='__main__':unittest.main()
