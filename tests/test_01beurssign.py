"""Offline tests: Yahoo, database and delivery are replaced by mocks."""
import contextlib
import io
import math
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import bot_01beurssign as b


def history(n=65):
    close = np.linspace(98, 100.123456, n)
    vol = np.full(n, 1000.)
    vol[-1] = 2000.1234
    return pd.DataFrame({'Close': close, 'Volume': vol},
                        index=pd.bdate_range(end='2026-11-12', periods=n))


class Tests(unittest.TestCase):
    def setUp(self):
        p = patch.object(b, 'today_str', return_value='2026-11-13')
        p.start(); self.addCleanup(p.stop)

    def analyse(self, us=False, data=None):
        with patch.object(b.yf, 'Ticker') as ticker, patch.object(b, '_insider_net_shares', return_value=20):
            ticker.return_value.history.return_value = history() if data is None else data
            return b.analyse_ticker('A', b.MODUS_CFG['live'], us)

    def test_vwap_known(self):
        self.assertAlmostEqual(b._vwap(np.array([10.,20.]), np.array([1.,3.]),2),17.5)

    def test_zero_vwap_volume(self):
        self.assertTrue(math.isnan(b._vwap(np.array([10.]),np.array([0.]),1)))

    def test_overhead_above_price(self):
        self.assertFalse(b._overhead_cleared(np.full(60,110.),np.ones(60),100,75))

    def test_overhead_below_price(self):
        self.assertTrue(b._overhead_cleared(np.linspace(80,90,60),np.ones(60),100,75))

    def test_precision_and_previous_volume(self):
        s=self.analyse()
        self.assertEqual(s.price,100.123456)
        self.assertEqual(s.vol_ratio_20d,2.0001234)
        self.assertEqual(s.score,3);self.assertEqual(s.max_score,3)
        self.assertEqual(s.signal_date,'2026-11-12')

    def test_repair_recovers_missing_last_close_same_session(self):
        raw=history();raw.iloc[-1,0]=np.nan
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.history.side_effect=[raw,history()]
            sig=b.analyse_ticker('A',b.MODUS_CFG['live'],False)
        self.assertEqual(sig.signal_date,'2026-11-12')
        self.assertIn('Yahoo-repair',sig.data_note)
        self.assertTrue(tk.return_value.history.call_args.kwargs['repair'])

    def test_repair_still_invalid_rejected(self):
        raw=history();raw.iloc[-1,0]=np.nan
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.history.return_value=raw
            with self.assertRaises(b.DataUnavailable):b.analyse_ticker('A',b.MODUS_CFG['live'],False)

    def test_repair_must_not_drop_bad_last_session(self):
        raw=history();raw.iloc[-1,0]=np.nan
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.history.side_effect=[raw,history().iloc[:-1]]
            with self.assertRaisesRegex(b.DataUnavailable,'laatste koersdag niet'):
                b.analyse_ticker('A',b.MODUS_CFG['live'],False)

    def test_valid_history_does_not_trigger_repair(self):
        with patch.object(b.yf,'Ticker') as tk:
            tk.return_value.history.return_value=history()
            sig=b.analyse_ticker('A',b.MODUS_CFG['live'],False)
        self.assertFalse(sig.data_note);tk.return_value.history.assert_called_once()

    def test_us_extra_point(self):
        s=self.analyse(True);self.assertEqual((s.score,s.max_score),(4,4))

    def test_sort_duplicate_and_timezone(self):
        h=history();h.index=h.index.tz_localize('Europe/Brussels')
        h=pd.concat([h,h.tail(1)]).sample(frac=1,random_state=1)
        r=b.prepare_history(h);self.assertEqual(len(r),65)
        self.assertTrue(r.index.is_monotonic_increasing)
        self.assertEqual(r.index[-1],pd.Timestamp('2026-11-12'))

    def test_current_future_excluded(self):
        h=history(); extra=h.tail(2).copy();extra.index=pd.to_datetime(['2026-11-13','2026-11-16'])
        self.assertEqual(len(b.prepare_history(pd.concat([h,extra]))),65)

    def test_bad_last_rejected(self):
        for col,value in [('Close',np.inf),('Close',0),('Volume',-1),('Volume',np.nan)]:
            h=history();h.iloc[-1,h.columns.get_loc(col)]=value
            with self.subTest(col=col,value=value),self.assertRaises(b.DataUnavailable):b.prepare_history(h)

    def test_short_missing_stale_and_zero_volume(self):
        stale=history();stale.index-=pd.Timedelta(days=10)
        zero=history();zero.Volume=0
        for h in [history(54),history().drop(columns='Volume'),stale,zero]:
            with self.assertRaises(b.DataUnavailable):b.prepare_history(h)

    def test_invalid_older_row_removed(self):
        h=history();h.iloc[0,0]=np.nan
        self.assertEqual(len(b.prepare_history(h)),64)

    def insider(self, frame):
        with patch.object(b.yf,'Ticker') as t:
            t.return_value.insider_transactions=frame
            return b._insider_net_shares('A')

    def transactions(self):
        now=pd.Timestamp.now(tz='UTC')
        return pd.DataFrame({'Start Date':[now-pd.Timedelta(days=2)]*2,
                             'Text':['Purchase','Sale'], 'Shares':[100,-30]})

    def test_insider_share_units_and_sign(self):
        self.assertEqual(self.insider(self.transactions()),70)

    def test_no_currency_fallback(self):
        df=self.transactions().rename(columns={'Shares':'Value'})
        self.assertTrue(math.isnan(self.insider(df)))

    def test_unknown_share_count(self):
        df=self.transactions();df.loc[1,'Shares']=np.nan
        self.assertTrue(math.isnan(self.insider(df)))

    def test_future_and_old_transactions_ignored(self):
        df=self.transactions();now=pd.Timestamp.now(tz='UTC')
        df['Start Date']=[now+pd.Timedelta(days=1),now-pd.Timedelta(days=100)]
        self.assertTrue(math.isnan(self.insider(df)))

    def test_transaction_column_and_no_text(self):
        df=self.transactions().rename(columns={'Text':'Transaction'})
        self.assertEqual(self.insider(df),70)
        self.assertTrue(math.isnan(self.insider(df.drop(columns='Transaction'))))

    def run_bot(self, candidate=True, db=True, email=True, error=None, missing=False, mode='live'):
        sig=self.analyse();sig.score=3 if candidate else 0
        with patch.multiple(b,
            bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt','tickers_042x.txt']),
            load_tickers_from_file=MagicMock(return_value=[] if missing else ['A','A']),
            analyse_ticker=MagicMock(return_value=sig,side_effect=error),
            send_email=MagicMock(return_value=email),log_selectie=MagicMock(return_value=db)), \
            patch.object(b.time,'sleep'), patch.object(b.yf,'Ticker') as yahoo, \
            contextlib.redirect_stdout(io.StringIO()):
            failed=None
            try:b.run_engine(mode)
            except RuntimeError as exc:failed=exc
            return b.send_email,b.log_selectie,b.analyse_ticker,failed

    def test_cache_db_date_and_no_telegram(self):
        mail,db,analyse,error=self.run_bot()
        self.assertIsNone(error);analyse.assert_called_once();self.assertEqual(db.call_count,2)
        self.assertEqual(db.call_args.kwargs['datum'],'2026-11-12')
        self.assertIn('2/2 selecties',mail.call_args.args[1])
        self.assertNotIn('send_telegram',b.__dict__)

    def test_valid_empty_selection(self):
        mail,db,_,error=self.run_bot(candidate=False)
        self.assertIsNone(error);db.assert_not_called();mail.assert_called_once()

    def test_data_error_visible(self):
        mail,_,_,error=self.run_bot(error=b.DataUnavailable('te kort'))
        self.assertIsNotNone(error);self.assertIn('te kort',mail.call_args.args[1])
        self.assertIn('[ONVOLLEDIG]',mail.call_args.args[0])

    def test_code_error_fails(self):
        _,_,_,error=self.run_bot(error=RuntimeError('bug'));self.assertIsNotNone(error)

    def test_empty_lists_fails_and_emails(self):
        mail,_,_,error=self.run_bot(missing=True)
        self.assertIsNotNone(error);self.assertIn('lege tickerlijst',mail.call_args.args[1])

    def test_database_false_and_exception_fail(self):
        self.assertIsNotNone(self.run_bot(db=False)[-1])

    def test_email_failure(self):
        self.assertIsNotNone(self.run_bot(email=False)[-1])

    def test_full_mode_uses_existing_strategy(self):
        _,db,_,error=self.run_bot(mode='full')
        self.assertIsNone(error)
        self.assertEqual(db.call_args.kwargs['strategie'],'bot_01beurssig_full')

    def test_partial_data_success_is_marked(self):
        sig=self.analyse()
        with patch.multiple(b,
            bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A','B']),
            analyse_ticker=MagicMock(side_effect=[sig,b.DataUnavailable('B ontbreekt')]),
            send_email=MagicMock(return_value=True),log_selectie=MagicMock(return_value=True)), \
            patch.object(b.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            b.run_engine('live')
            self.assertIn('[ONVOLLEDIG]',b.send_email.call_args.args[0])
            self.assertIn('B ontbreekt',b.send_email.call_args.args[1])

    def test_database_exception_reported(self):
        sig=self.analyse()
        with patch.multiple(b,
            bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A']),
            analyse_ticker=MagicMock(return_value=sig),send_email=MagicMock(return_value=True),
            log_selectie=MagicMock(side_effect=RuntimeError('db test'))), \
            patch.object(b.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):b.run_engine('live')
            self.assertIn('db test',b.send_email.call_args.args[1])

    def test_workflows_keep_services_and_telegram_disabled(self):
        from pathlib import Path
        import yaml
        for name,mode in [('run_01beurssign.yml','live'),('run_01beurssignfull.yml','full')]:
            doc=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows'/name).read_text())
            job=next(iter(doc['jobs'].values()))
            step=job['steps'][-1];env=step['env']
            self.assertIn('EMAIL_PASS',env);self.assertIn('SUPABASE_DB_URL',env)
            installs = [step.get('run', '') for step in job['steps'] if 'pip install' in step.get('run', '')]
            self.assertTrue(any('scikit-learn' in command.split() for command in installs))
            preflight = [step for step in job['steps'] if step.get('name') == 'Controleer Yahoo-repair dependency']
            self.assertEqual(preflight[0]['run'], 'python -c "from sklearn.cluster import DBSCAN"')

            self.assertNotIn('TELEGRAM_TOKEN',env)
            self.assertEqual(step['run'],f'python bot_01beurssign.py {mode}')
            self.assertFalse(doc['concurrency']['cancel-in-progress'])

    def test_candidate_count_not_capped(self):
        sigs=[self.analyse() for _ in range(7)]
        msg=b.format_bericht('X',sigs[:5],sigs,b.MODUS_CFG['live'])
        self.assertIn('7 kandidaten, 5 topselecties',msg)

if __name__=='__main__':unittest.main()
