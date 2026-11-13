"""Synthetic MR/ORB tests. All delivery, network and DB effects are mocked."""
import os,sys,json,math,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
import pandas as pd
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
for name in ('yfinance','requests','dotenv'):
    try:__import__(name)
    except ImportError:sys.modules[name]=MagicMock()
import bot_00mr as b


def eod(n=240,ticker='A'):
    close=np.linspace(80,100,n)
    return pd.DataFrame(dict(Date=pd.bdate_range(end='2026-11-12',periods=n),Ticker=ticker,
        Open=close,High=close+1,Low=close-1,Close=close,Volume=1000.))


def intraday(start='2026-11-13T14:30:00Z'):
    return pd.DataFrame(dict(Open=[100,100.5,101],High=[101,101.5,101.7],Low=[99,100,100.5],
        Close=[100,101.2,101.6],Volume=[100,100,200]),index=pd.date_range(start,periods=3,freq='15min'))


def metadata(start='2026-11-13T14:30:00Z',end='2026-11-13T21:00:00Z',zone='America/New_York'):
    return dict(exchangeTimezoneName=zone,currency='USD',currentTradingPeriod=dict(regular=dict(
        start=int(pd.Timestamp(start).timestamp()),end=int(pd.Timestamp(end).timestamp()))))


def mr_signal():return b.MRSignaal('A',100.123456,.1,5,105,90,2,95,105,1,'X','2026-11-12')

class MR(unittest.TestCase):
    def row(self,**kw):
        row=dict(Date=pd.Timestamp('2026-11-12'),Ticker='A',Close=100.123456,High=101,Low=99,
            IBS=.1,RSI3=5,MA20=105,MA200=90,ATR14=2)
        row.update(kw);return row
    def test_original_conditions_and_precision(self):
        entries,_=b.generate_mr_signals(pd.DataFrame([self.row()]),'X')
        self.assertEqual(len(entries),1);self.assertEqual(entries[0].price,100.123456)
        self.assertEqual(entries[0].stop,98);self.assertEqual(entries[0].signal_date,'2026-11-12')
    def test_last_row_not_last_non_nan_from_older_day(self):
        a=self.row(Date=pd.Timestamp('2026-11-11'));z=self.row(RSI3=np.nan)
        entries,_=b.generate_mr_signals(pd.DataFrame([a,z]),'X');self.assertFalse(entries)
    def test_negative_stop_rejected(self):
        self.assertFalse(b.generate_mr_signals(pd.DataFrame([self.row(Low=.1,ATR14=2)]),'X')[0])
    def test_nonfinite_rejected(self):
        self.assertFalse(b.generate_mr_signals(pd.DataFrame([self.row(ATR14=np.inf)]),'X')[0])
    def test_no_target_below_entry(self):
        self.assertFalse(b.generate_mr_signals(pd.DataFrame([self.row(MA20=99)]),'X')[0])
    def test_exit_condition_preserved(self):
        _,exits=b.generate_mr_signals(pd.DataFrame([self.row(IBS=.8)]),'X')
        self.assertEqual(len(exits),1);self.assertIn('IBS',exits[0].reden)
    def test_indicator_sorting(self):
        g=eod();a=b.add_eod_indicators(g);z=b.add_eod_indicators(g.sample(frac=1))
        np.testing.assert_allclose(a.MA200,z.MA200,equal_nan=True)

class Cleaning(unittest.TestCase):
    def test_bad_ohlcv_dropped(self):
        g=eod();g.loc[0,'High']=0;g.loc[1,'Volume']=-1;g.loc[2,'Close']=np.inf
        self.assertEqual(len(b.clean_prices(g)),237)
    def test_local_day_preserved(self):
        g=eod();g.Date=g.Date.dt.tz_localize('Europe/Brussels')
        self.assertEqual(b.clean_prices(g).Date.iloc[-1],pd.Timestamp('2026-11-12'))
    def test_duplicates_sorted(self):
        g=eod();result=b.clean_prices(pd.concat([g,g.tail(1)]).sample(frac=1))
        self.assertEqual(len(result),240);self.assertTrue(result.Date.is_monotonic_increasing)
    def test_missing_open_not_invented(self):self.assertTrue(b.clean_prices(eod().drop(columns='Open')).empty)
    def test_intraday_utc_preserved(self):
        result=b.clean_prices(intraday(),True,'America/New_York')
        self.assertEqual(str(result.Date.dt.tz),'UTC')
    def test_naive_intraday_requires_timezone(self):
        g=intraday();g.index=g.index.tz_localize(None)
        with self.assertRaises(ValueError):b.clean_prices(g,True)

class ORB(unittest.TestCase):
    def analyse(self,raw=None,md=None,now='2026-11-13T15:15:00Z'):
        return b.orb_for_ticker('A',intraday() if raw is None else raw,metadata() if md is None else md,'X',pd.Timestamp(now))
    def test_breakout_and_prior_volume_only(self):
        s,status=self.analyse();self.assertTrue(s.breakout);self.assertEqual(s.vol_ratio,2)
        self.assertEqual(s.session_start,'2026-11-13T14:30:00+00:00');self.assertEqual(status[0],'ok')
    def test_missing_opening_candle_not_replaced_by_later_bar(self):
        with self.assertRaises(b.ORBUnavailable):self.analyse(intraday().iloc[1:])
    def test_unfinished_last_candle_excluded(self):
        g=intraday();g.iloc[-1,g.columns.get_loc('Close')]=10000
        g.iloc[-1,g.columns.get_loc('High')]=10001
        s,_=self.analyse(g,now='2026-11-13T15:07:00Z')
        self.assertEqual(s.current,101.2);self.assertFalse(s.breakout)
    def test_wait_for_two_completed_candles(self):
        s,status=self.analyse(now='2026-11-13T14:50:00Z');self.assertIsNone(s);self.assertEqual(status[0],'waiting')
    def test_before_open_is_not_data_error(self):
        s,status=self.analyse(now='2026-11-13T13:00:00Z');self.assertIsNone(s);self.assertEqual(status[0],'closed')
    def test_after_close_not_actionable(self):self.assertEqual(self.analyse(now='2026-11-13T21:01:00Z')[1][0],'closed')
    def test_yesterday_session_not_reused_today(self):self.assertEqual(self.analyse(now='2026-11-14T15:15:00Z')[1][0],'closed')
    def test_missing_session_metadata_rejected(self):
        with self.assertRaises(b.ORBUnavailable):self.analyse(md={})
    def test_no_timezone_guess(self):
        md=metadata();md['currentTradingPeriod']['regular']['start']=pd.Timestamp('2026-11-13 14:30')
        with self.assertRaises(b.ORBUnavailable):self.analyse(md=md)
    def test_dst_uses_provider_open(self):
        s,_=self.analyse(intraday('2026-07-13T13:30:00Z'),metadata('2026-07-13T13:30:00Z','2026-07-13T20:00:00Z'),now='2026-07-13T14:15:00Z')
        self.assertTrue(s.breakout);self.assertIn('13:30',s.session_start)
    def test_formatted_metadata_timestamps_supported(self):
        md=metadata();md['currentTradingPeriod']['regular']={'start':pd.Timestamp('2026-11-13T09:30:00-05:00'),'end':pd.Timestamp('2026-11-13T16:00:00-05:00')}
        self.assertTrue(self.analyse(md=md)[0].breakout)
    def test_stale_completed_candles_rejected(self):
        with self.assertRaises(b.ORBUnavailable):self.analyse(now='2026-11-13T16:00:00Z')
    def test_premarket_not_opening_range(self):
        early=intraday('2026-11-13T13:00:00Z')
        s,_=self.analyse(pd.concat([early,intraday()]))
        self.assertEqual(s.orb_high,101)
    def test_zero_volume_baseline_rejected(self):
        g=intraday();g.iloc[:2,g.columns.get_loc('Volume')]=0
        with self.assertRaises(b.ORBUnavailable):self.analyse(g)
    def test_target_already_exceeded_not_recommended(self):
        g=intraday();g.iloc[-1,g.columns.get_loc('Close')]=110;g.iloc[-1,g.columns.get_loc('High')]=111
        self.assertIsNone(self.analyse(g)[0])
    def test_off_grid_bars_rejected(self):
        g=intraday();g.index=g.index+pd.Timedelta(minutes=1)
        with self.assertRaises(b.ORBUnavailable):self.analyse(g)
    def test_missing_internal_candle_rejected(self):
        g=intraday().drop(intraday().index[1])
        with self.assertRaises(b.ORBUnavailable):self.analyse(g)
    def test_duplicate_ticker_requested_once(self):
        tk=MagicMock();tk.history.return_value=intraday();tk.get_history_metadata.return_value=metadata()
        with patch.object(b.yf,'Ticker',return_value=tk) as ticker:
            cache={};diag={}
            one=b.generate_orb_signals(['A','A'],'X',diag,cache,pd.Timestamp('2026-11-13T15:15Z'))
            two=b.generate_orb_signals(['A'],'Y',diag,cache,pd.Timestamp('2026-11-13T15:15Z'))
        self.assertEqual(ticker.call_count,1);self.assertEqual(one[0].exchange,'X');self.assertEqual(two[0].exchange,'Y')

class Runs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=Path.cwd();os.chdir(self.tmp.name)
        self.addCleanup(self.restore)
        p=patch.object(b,'today_str',return_value='2026-11-13');p.start();self.addCleanup(p.stop)
        p=patch.dict(os.environ,{},clear=True);p.start();self.addCleanup(p.stop)
    def restore(self):os.chdir(self.old);self.tmp.cleanup()
    def run_eod(self,g=None,db=True,email=True,fail=False,entry=True):
        if g is None:g=eod()
        def generate(frame,exchange):return ([mr_signal()] if entry else [],[])
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['x']),
            load_tickers_from_file=MagicMock(return_value=['A']),label_voor=MagicMock(return_value='X'),
            download_eod=MagicMock(return_value=g),generate_mr_signals=MagicMock(side_effect=RuntimeError('analysis') if fail else generate),
            send_email=MagicMock(return_value=email),send_telegram=MagicMock(),log_selectie=MagicMock(return_value=db)):
            error=None
            try:b.run_eod()
            except RuntimeError as exc:error=exc
            return dict(error=error,payload=json.loads(Path('mr_eod_scan.json').read_text()),
                email=b.send_email.call_args,telegram=b.send_telegram.call_count)
    def test_success_logs_and_emails_no_telegram(self):
        r=self.run_eod();self.assertIsNone(r['error']);self.assertEqual(r['payload']['database']['saved'],1);self.assertEqual(r['telegram'],0)
    def test_state_is_not_mutated(self):
        Path('mr_portfolio.json').write_text(json.dumps(dict(cash=50015.8,positions={},daily_start='old',daily_pnl=-10)))
        before=Path('mr_portfolio.json').read_bytes();self.run_eod();self.run_eod()
        self.assertEqual(before,Path('mr_portfolio.json').read_bytes())
    def test_corrupt_state_reported_not_reset(self):
        Path('mr_portfolio.json').write_text('broken');r=self.run_eod()
        self.assertIsNotNone(r['error']);self.assertEqual(Path('mr_portfolio.json').read_text(),'broken')
    def test_no_candidates_still_emails(self):
        r=self.run_eod(entry=False);self.assertIsNone(r['error']);self.assertIsNotNone(r['email'])
    def test_no_data_fails_after_report(self):self.assertIsNotNone(self.run_eod(pd.DataFrame())['error'])
    def test_database_failure_fails(self):self.assertIsNotNone(self.run_eod(db=False)['error'])
    def test_email_failure_fails(self):self.assertIsNotNone(self.run_eod(email=False)['error'])
    def test_analysis_error_visible(self):
        r=self.run_eod(fail=True);self.assertIsNotNone(r['error']);self.assertEqual(r['payload']['coverage']['error'],1)
    def test_closed_markets_do_not_fail_orb(self):
        def generate(tickers,exchange,diagnostics,cache,asof):
            diagnostics['A']=('closed','test');return []
        with patch.object(b,'ORB_EXCHANGES',{'X':'x'}),patch.object(b,'load_tickers_from_file',return_value=['A']),\
             patch.object(b,'generate_orb_signals',side_effect=generate),patch.object(b,'send_email',return_value=True) as mail,\
             patch.object(b,'send_telegram') as telegram,patch.object(b,'log_selectie') as db:
            b.run_orb()
            mail.assert_called_once();telegram.assert_not_called();db.assert_not_called()
            payload=json.loads(Path('mr_orb_scan.json').read_text())
            self.assertTrue(payload['complete']);self.assertEqual(payload['coverage']['closed'],1)

if __name__=='__main__':unittest.main()
