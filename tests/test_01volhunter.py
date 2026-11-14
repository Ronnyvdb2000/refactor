"""Offline synthetic histories; network, waits, email, DB mocked."""
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd
import bot_01volhunter as b


def history(n=125,scale=1):
    dates=pd.bdate_range(end='2026-11-13',periods=n)
    c=np.r_[np.linspace(70,110,n-6),[109,108,107,106,105,104.123456]]*scale
    return pd.DataFrame({'Date':dates,'Open':c+2*scale,'High':c+10*scale,
                         'Low':c-1*scale,'Close':c,'Volume':1000.})


def signal(t='A',score=10):
    return b.VolSignaal(t,.0123456,.001,10,.1,5,.01,-3,.0108456,.0148456,score,'2026-11-13')


class Tests(unittest.TestCase):
    def setUp(self):
        p=patch.object(b,'today_str',return_value='2026-11-14');p.start();self.addCleanup(p.stop)

    def test_signal_known_and_full_precision(self):
        g=b.prepare_history(history());high,s=b.evaluate_history('A',g)
        self.assertTrue(high);self.assertIsNotNone(s)
        self.assertEqual(s.price,104.123456)
        self.assertAlmostEqual(s.score,s.atr_pct*(1-s.ibs))
        self.assertAlmostEqual(s.stop,s.price-1.5*s.atr)
        self.assertEqual(s.signal_date,'2026-11-13')

    def test_tiny_price_not_rounded_to_zero(self):
        s=b.analyse_ticker_df('A',history(scale=.00001))
        self.assertGreater(s.stop,0);self.assertLess(s.price,.01)

    def test_high_atr_non_candidate_is_counted(self):
        with patch.object(b,'RSI_MAX',0):
            high,s=b.evaluate_history('A',b.prepare_history(history()))
            self.assertTrue(high);self.assertIsNone(s)

    def test_atr_band(self):
        with patch.object(b,'MIN_ATR_PCT',30):
            high,s=b.evaluate_history('A',b.prepare_history(history()))
            self.assertFalse(high);self.assertIsNone(s)

    def test_ibs_filter(self):
        with patch.object(b,'IBS_MAX',.01):self.assertIsNone(b.analyse_ticker_df('A',history()))

    def test_trend_filter_toggle(self):
        g=history();g.loc[:118,['Open','High','Low','Close']]*=2
        self.assertIsNone(b.analyse_ticker_df('A',g))
        with patch.object(b,'VEREIST_UPTREND',False):self.assertIsNotNone(b.analyse_ticker_df('A',g))

    def test_empty_and_missing_fields(self):
        for g in [pd.DataFrame(),history().drop(columns='High'),history().drop(columns='Volume')]:
            with self.subTest(cols=list(g.columns)),self.assertRaises(b.DataUnavailable):b.prepare_history(g)

    def test_bad_latest(self):
        for value in [np.nan,np.inf,0,-1]:
            g=history();g.loc[len(g)-1,'Close']=value
            with self.subTest(value=value),self.assertRaises(b.InvalidLatest):b.prepare_history(g)

    def test_incoherent_bar(self):
        g=history();g.loc[len(g)-1,'High']=10
        with self.assertRaises(b.InvalidLatest):b.prepare_history(g)

    def test_latest_zero_volume(self):
        g=history();g.loc[len(g)-1,'Volume']=0
        with self.assertRaises(b.DataUnavailable):b.prepare_history(g)

    def test_old_bad_row_tail_enough(self):
        g=history();g.loc[2,'Close']=np.nan
        result=b.prepare_history(g)
        self.assertEqual(len(result),122);self.assertIn('3 ',result.attrs['data_note'])

    def test_recent_bad_row_no_bridge(self):
        g=history();g.loc[100,'Close']=np.nan
        with self.assertRaises(b.DataUnavailable):b.prepare_history(g)

    def test_long_gap_truncates(self):
        g=history();g.loc[:80,'Date']-=pd.Timedelta(days=30)
        with self.assertRaises(b.DataUnavailable):b.prepare_history(g)

    def test_sorted_deduplicated(self):
        g=history();g=pd.concat([g,g.tail(1)],ignore_index=True).iloc[::-1]
        result=b.prepare_history(g)
        self.assertTrue(result.index.is_monotonic_increasing);self.assertEqual(len(result),125)

    def test_stale_rejected(self):
        g=history();g.Date-=pd.Timedelta(days=10)
        with self.assertRaises(b.DataUnavailable):b.prepare_history(g)

    def test_today_future_excluded(self):
        g=history();future=g.tail(1).copy();future.Date=pd.Timestamp('2026-11-16')
        self.assertEqual(b.prepare_history(pd.concat([g,future])).index[-1],pd.Timestamp('2026-11-13'))

    def test_local_timezone_preserved(self):
        g=history();g.Date=g.Date.dt.tz_localize('Europe/Brussels')
        self.assertEqual(b.prepare_history(g).index[-1],pd.Timestamp('2026-11-13'))

    def test_zero_range_ibs_unknown(self):
        g=history();i=len(g)-1;g.loc[i,['Open','High','Low']]=g.loc[i,'Close']
        with self.assertRaises(b.DataUnavailable):b.analyse_ticker_df('A',g)

    def test_config_invalid(self):
        for key,value in [('BATCH_SIZE',0),('TOP_N',0),('IBS_MAX',2),('MIN_ATR_PCT',np.nan)]:
            with patch.object(b,key,value),self.assertRaises(ValueError):b.validate_config()

    def test_multiindex_both_orders(self):
        g=history().set_index('Date')
        one=pd.concat({'A':g},axis=1)
        for raw in [one,one.swaplevel(axis=1)]:
            result=b._normalise(raw,'A');self.assertEqual(result.Close.iloc[-1],104.123456)

    def test_union_calendar_empty_rows(self):
        g=history().set_index('Date');g.loc[pd.Timestamp('2026-11-14')]=np.nan
        result=b._normalise(g,'A');self.assertEqual(len(result),125)

    def test_missing_batch_retried(self):
        with patch.object(b.yf,'download',return_value=pd.DataFrame()),patch.object(b.yf,'Ticker') as tk,patch.object(b.time,'sleep'):
            tk.return_value.history.return_value=history().set_index('Date')
            out=b.download_batch(['A']);self.assertIn('A',out);tk.assert_called_once_with('A')

    def test_repair_same_day(self):
        g=history().set_index('Date');g.loc[g.index[-1],'Close']=np.nan
        with patch.object(b.yf,'download',return_value=g),patch.object(b.yf,'Ticker') as tk,patch.object(b.time,'sleep'):
            tk.return_value.history.return_value=history().set_index('Date')
            out=b.download_batch(['A'])
            self.assertIn('Yahoo-repair',out['A'].attrs['data_note'])
            self.assertTrue(tk.return_value.history.call_args.kwargs['repair'])

    def test_repair_older_day_rejected(self):
        g=history().set_index('Date');g.loc[g.index[-1],'Close']=np.nan
        with patch.object(b.yf,'download',return_value=g),patch.object(b.yf,'Ticker') as tk,patch.object(b.time,'sleep'):
            tk.return_value.history.return_value=history().set_index('Date').iloc[:-1]
            issues=[];self.assertEqual(b.download_batch(['A'],issues=issues),{})
            self.assertIn('laatste koersdag',issues[-1])

    def test_repair_invalid_stays_rejected(self):
        g=history().set_index('Date');g.loc[g.index[-1],'Close']=np.nan
        with patch.object(b.yf,'download',return_value=g),patch.object(b.yf,'Ticker') as tk,patch.object(b.time,'sleep'):
            tk.return_value.history.return_value=g
            self.assertEqual(b.download_batch(['A']),{})

    def test_rate_recovers(self):
        fn=MagicMock(side_effect=[b.YFRateLimitError(),7])
        with patch.object(b.time,'sleep') as sleep:
            self.assertEqual(b.yahoo_request(fn,b.RequestGate()),7)
            self.assertEqual(sum(c.args[0] for c in sleep.call_args_list),60)

    def test_rate_exhaustion_stops(self):
        gate=b.RequestGate();fn=MagicMock(side_effect=b.YFRateLimitError())
        with patch.object(b.time,'sleep') as sleep:
            with self.assertRaises(b.ProviderStop):b.yahoo_request(fn,gate)
            self.assertEqual(fn.call_count,4)
            self.assertEqual(sum(c.args[0] for c in sleep.call_args_list),540)
            self.assertTrue(all(c.args[0]<=30 for c in sleep.call_args_list))
            with self.assertRaises(b.ProviderStop):b.yahoo_request(fn,gate)
            self.assertEqual(fn.call_count,4)

    def test_deadline_no_request(self):
        fn=MagicMock()
        with patch.object(b.time,'monotonic',return_value=100),self.assertRaises(b.ProviderStop):
            b.yahoo_request(fn,b.RequestGate(deadline=90))
        fn.assert_not_called()

    def test_normal_error_not_retried(self):
        fn=MagicMock(side_effect=ValueError('bug'))
        with self.assertRaises(ValueError):b.yahoo_request(fn,b.RequestGate())
        fn.assert_called_once()

    def live(self, candidate=True, db=True, email=True, empty=False, blocked=False):
        g=b.prepare_history(history())
        def download(ts,**kw):
            if blocked:kw['gate'].blocked_reason='Yahoo blokkade'
            return {} if empty else {t:g for t in ts}
        with patch.multiple(b,bouw_bestandslijst_a=MagicMock(return_value=['tickers_041a.txt','tickers_042a.txt']),
            load_tickers_from_file=MagicMock(return_value=['B','A','A']),
            download_batch=MagicMock(side_effect=download),
            evaluate_history=MagicMock(side_effect=lambda t,g:(True,signal(t) if candidate else None)),
            log_selectie=MagicMock(return_value=db),send_email=MagicMock(return_value=email)),patch.object(b.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            error=None
            try:b.run_live()
            except RuntimeError as exc:error=exc
            return b.send_email,b.log_selectie,b.evaluate_history,b.download_batch,error

    def test_live_cache_and_stable_ties(self):
        mail,db,analyse,download,error=self.live()
        self.assertIsNone(error);self.assertEqual(analyse.call_count,2);download.assert_called_once()
        self.assertEqual(db.call_args_list[0].kwargs['ticker'],'A')
        self.assertEqual(db.call_args.kwargs['datum'],'2026-11-13')
        self.assertIn('4/4',mail.call_args.args[1]);self.assertNotIn('send_telegram',b.__dict__)

    def test_high_atr_count_independent_of_candidates(self):
        mail,db,_,_,error=self.live(candidate=False)
        self.assertIsNone(error);db.assert_not_called()
        self.assertIn('2 binnen ATR-band | 0 kandidaten',mail.call_args.args[1])

    def test_live_no_data_fails_emails(self):
        mail,db,_,_,error=self.live(empty=True)
        self.assertIsNotNone(error);mail.assert_called_once();db.assert_not_called()

    def test_live_blockade_red(self):self.assertIsNotNone(self.live(blocked=True)[-1])
    def test_live_db_failure_red(self):self.assertIsNotNone(self.live(db=False)[-1])
    def test_live_mail_failure_red(self):self.assertIsNotNone(self.live(email=False)[-1])

    def test_workflow_repair_and_services(self):
        s=Path('.github/workflows/run_01volatieledipscan.yml').read_text()
        for item in ['scikit-learn','DBSCAN','EMAIL_USER','SUPABASE_DB_URL','PYTHONUNBUFFERED','BATCH_SIZE: "50"']:self.assertIn(item,s)
        self.assertNotIn('TELEGRAM_',s)

if __name__=='__main__':unittest.main()
