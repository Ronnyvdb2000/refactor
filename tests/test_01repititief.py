"""Offline tests; network, email and database are mocked."""
import contextlib
import io
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd
import bot_01repititief as b


def frame(start='2019-01-01', end='2026-11-13', ticker='A'):
    d = pd.bdate_range(start,end)
    r = np.random.default_rng(6).normal(.0005,.008,len(d))
    return pd.DataFrame({'Date':d,'Close':100*np.exp(np.cumsum(r)), 'Ticker':ticker})


def candidate(ticker='A', mean=.01, p=.001):
    stat={'gemiddelde':mean,'win_rate':.6,'p_waarde':p,'n':100}
    return b.SeizoenKandidaat(ticker, .0123456, stat, None, None, 7., '2026-11-13','2026-11-16')


class Tests(unittest.TestCase):
    def setUp(self):
        p=patch.object(b,'today_str',return_value='2026-11-14');p.start();self.addCleanup(p.stop)

    def test_bh_known_step_up(self):
        self.assertEqual(b.bh_correctie([.01,.04,.03,.9],.05),[True,False,False,False])
        self.assertEqual(b.bh_correctie([.01,.03,.04],.05),[True]*3)

    def test_bh_invalid_keeps_denominator(self):
        self.assertEqual(b.bh_correctie([.03,float('nan')],.05),[False,False])
        self.assertEqual(b.bh_correctie([float('inf'),-1],.05),[False,False])

    def test_constant_not_significant(self):
        self.assertEqual(b.bereken_bucket_stats(np.ones(20))['p_waarde'],1)
        self.assertIsNone(b.bereken_bucket_stats([1,2,3,np.nan]))

    def test_stats_finite_only(self):
        a=np.array([-.01,.02,.03,-.02,.04])
        self.assertAlmostEqual(b.bereken_bucket_stats(np.r_[a,np.inf])['gemiddelde'],a.mean())

    def test_negative_filtered_after_bh(self):
        self.assertEqual(b.pas_bh_toe_op_beurs([candidate(mean=-.1)],.05),[])
        self.assertEqual(len(b.pas_bh_toe_op_beurs([candidate()],.05)),1)

    def test_weekend_targets_monday(self):
        self.assertEqual(str(b.target_day().date()),'2026-11-16')

    def test_precision_and_actual_date(self):
        g=frame();g.loc[len(g)-1,'Close']=.0123456
        k=b.analyseer_ticker('A',g)
        self.assertEqual(k.price,.0123456)
        self.assertEqual(k.signal_date,'2026-11-13')

    def test_stale_rejected(self):
        with self.assertRaises(b.DataUnavailable):b.prepare_history(frame(end='2026-11-01'))

    def test_bad_latest_rejected(self):
        g=frame();g.loc[len(g)-1,'Close']=np.nan
        with self.assertRaises(b.DataUnavailable):b.prepare_history(g)

    def test_unfinished_rows_excluded(self):
        g=frame(end='2026-11-16')
        self.assertLess(b.prepare_history(g).index.max(),pd.Timestamp('2026-11-14'))

    def test_timezone_local_day_preserved(self):
        g=frame();g['Date']=g.Date.dt.tz_localize('Europe/Brussels')
        self.assertEqual(b.prepare_history(g).index[-1],pd.Timestamp('2026-11-13'))

    def test_bad_older_row_not_bridged(self):
        g=frame();g.loc[2,'Close']=np.nan
        clean=b.prepare_history(g);r=b.daily_returns(clean)
        self.assertNotIn(clean.index[2],r.index);self.assertNotIn(clean.index[3],r.index)

    def test_short_history_rejected(self):
        with self.assertRaises(b.DataUnavailable):b.analyseer_ticker('A',frame(start='2025-01-01'))

    def test_training_cannot_see_test_year(self):
        g=frame(start='2010-01-01').set_index('Date');r=b.daily_returns(g)
        a=b._train_bucket_stats_per_ticker(r,2021,'maand')
        r.loc[r.index.year>=2021]=999
        self.assertEqual(a,b._train_bucket_stats_per_ticker(r,2021,'maand'))

    def test_partial_month_not_closed(self):
        g=frame().set_index('Date')
        self.assertEqual(b.bucket_trades('maand',11,g,2026),[])
        self.assertEqual(b.bucket_trades('kwartaal',4,g,2026),[])

    def test_month_includes_previous_close(self):
        g=frame(start='2025-12-01',end='2026-04-02').set_index('Date')
        trade=b.bucket_trades('maand',2,g,2026)[0]
        self.assertEqual(trade[0],pd.Timestamp('2026-01-30'))
        self.assertEqual(trade[1],pd.Timestamp('2026-02-27'))
        self.assertAlmostEqual(trade[2],g.loc[trade[0],'Close']*1.001)

    def test_no_previous_year_model_fill(self):
        g=frame(start='2025-12-01',end='2026-04-02').set_index('Date')
        self.assertEqual(b.bucket_trades('maand',1,g,2026),[])
        self.assertTrue(all(t[0].year==2026 for t in b.bucket_trades('weekdag',3,g,2026)))

    def test_pnl_no_forced_share_and_no_tax(self):
        self.assertEqual(b._bereken_pnl(5000,6000)['size'],0)
        r=b._bereken_pnl(100,110)
        self.assertLessEqual(r['size']*100+b.trade_cost(r['size']*100),2500)
        self.assertEqual(r['tax'],0)
        self.assertAlmostEqual(r['net'],r['size']*10-b.trade_cost(r['size']*100)-b.trade_cost(r['size']*110))

    def test_invalid_pnl_rejected(self):
        with self.assertRaises(ValueError):b._bereken_pnl(100,float('inf'))

    def test_historical_pf_no_fake_infinity(self):
        g=frame(start='2020-01-01').set_index('Date')
        with patch.object(b,'_trades_voor_bucket',return_value=[(100,110)]):
            check=b.historische_check('maand',2,g)
            self.assertIsNone(check['pf'])
            self.assertIn('beschrijvend',check['method'])

    def live(self, candidates=True, db=True, email=True, empty=False):
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt','tickers_042x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A','A']),
            download_history=MagicMock(return_value=pd.DataFrame() if empty else frame()),
            analyseer_ticker=MagicMock(return_value=candidate()),
            pas_bh_toe_op_beurs=MagicMock(side_effect=lambda ks,a: [b.SeizoenSignaal('A',.0123456,.01,ks[0].weekdag,None,None,{'weekdag'},7,signal_date='2026-11-13',target_date='2026-11-16')] if ks and candidates else []),
            historische_check=MagicMock(return_value=None),log_selectie=MagicMock(return_value=db),
            send_email=MagicMock(return_value=email)),contextlib.redirect_stdout(io.StringIO()):
            error=None
            try:b.run_live_engine()
            except RuntimeError as exc:error=exc
            return b.send_email,b.log_selectie,b.analyseer_ticker,error

    def test_live_deduplicates_and_confirms(self):
        mail,db,analyse,error=self.live()
        self.assertIsNone(error);self.assertEqual(analyse.call_count,1)
        self.assertEqual(db.call_count,2)
        self.assertIn('2/2',mail.call_args.args[1])
        self.assertNotIn('send_telegram',b.__dict__)

    def test_live_empty_selection_emails(self):
        mail,db,_,error=self.live(candidates=False)
        self.assertIsNone(error);db.assert_not_called();mail.assert_called_once()
        self.assertIn('0/0',mail.call_args.args[1])

    def test_live_db_failure_red_and_emails(self):
        mail,_,_,error=self.live(db=False)
        self.assertIsNotNone(error);self.assertIn('[ONVOLLEDIG]',mail.call_args.args[0])

    def test_live_mail_failure_red(self):
        self.assertIsNotNone(self.live(email=False)[-1])

    def test_live_no_data_red(self):
        # Use real analysis so empty data cannot be accepted by a mock.
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=[]),send_email=MagicMock(return_value=True)),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):b.run_live_engine()
            b.send_email.assert_called_once()

    def test_output_not_eur_or_walkforward(self):
        sig=b.SeizoenSignaal('A',.0123456,.01,candidate().weekdag,None,None,{'weekdag'},7,signal_date='2026-11-13',target_date='2026-11-16')
        msg=b.format_bericht('test',[sig],[candidate()])
        self.assertIn('0.0123 bronkoerseenheden',msg)
        self.assertIn('geen onafhankelijke',msg)
        self.assertNotIn('EUR',msg)

    def test_backtest_empty_csv_and_no_services(self):
        with tempfile.TemporaryDirectory() as tmp,patch.multiple(b,
            bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt']),load_tickers_from_file=MagicMock(return_value=['A']),
            download_history=MagicMock(return_value=frame(start='2010-01-01')),
            bh_correctie=MagicMock(side_effect=lambda ps,a:[False]*len(ps)),send_email=MagicMock(),log_selectie=MagicMock()),contextlib.redirect_stdout(io.StringIO()):
            old=os.getcwd()
            try:
                os.chdir(tmp);b.run_backtest()
                out=pd.read_csv('seizoen_backtest_trades.csv')
                self.assertTrue(out.empty);self.assertIn('entry_date',out.columns)
                b.send_email.assert_not_called();b.log_selectie.assert_not_called()
            finally:os.chdir(old)

    def test_backtest_dated_fills_and_exchange_families(self):
        stat={'gemiddelde':.01,'p_waarde':.001,'win_rate':.6,'n':100}
        with tempfile.TemporaryDirectory() as tmp,patch.multiple(b,
            BACKTEST_END='2021-12-31',
            bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt','tickers_042x.txt']),
            load_tickers_from_file=MagicMock(side_effect=[['A'],['A']]),
            download_history=MagicMock(return_value=frame(start='2010-01-01',end='2022-01-04')),
            _train_bucket_stats_per_ticker=MagicMock(return_value={2:stat}),
            bh_correctie=MagicMock(side_effect=lambda ps,a:[True]*len(ps))) ,contextlib.redirect_stdout(io.StringIO()):
            old=os.getcwd()
            try:
                os.chdir(tmp);b.run_backtest();out=pd.read_csv('seizoen_backtest_trades.csv')
                self.assertFalse(out.empty)
                self.assertEqual(out.beurs.nunique(),2)
                self.assertTrue((pd.to_datetime(out.entry_date)<pd.to_datetime(out.exit_date)).all())
                self.assertTrue((out.tax==0).all())
                self.assertTrue((pd.to_datetime(out.exit_date)<=pd.Timestamp('2021-12-31')).all())
            finally:os.chdir(old)

    def test_workflow_services(self):
        s=Path('.github/workflows/run_01repititief.yml').read_text()
        self.assertNotIn('TELEGRAM_',s)
        for key in ('EMAIL_USER','SUPABASE_DB_URL','PYTHONUNBUFFERED','timeout-minutes'):self.assertIn(key,s)

if __name__=='__main__':unittest.main()
