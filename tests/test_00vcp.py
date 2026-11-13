"""Regression tests with synthetic histories and mocked external effects."""
import sys
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import pandas as pd
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
for name in ('yfinance','requests','dotenv'):
    try: __import__(name)
    except ImportError: sys.modules[name]=MagicMock()
import bot_00vcp as b


def history(n=340):
    return pd.DataFrame(dict(Date=pd.bdate_range(end='2026-11-12',periods=n),Ticker='A',
        Open=100.,High=101.,Low=99.,Close=100.,Volume=1000.))

def pattern():
    points={0:90,10:100,30:80,45:96,60:86,75:94,85:89,119:101}
    close=np.interp(np.arange(120),list(points),list(points.values()))
    close=np.r_[np.full(20,90.),close]
    volume=np.full(140,1000.);volume[65:95]=700;volume[95:]=400;volume[-1]=2000
    return [pd.Series(x) for x in (close,close+.2,close-.2,volume)]

def signal():
    v=b.VCPResultaat([],2,True,True,True,5,100,92,True,2,True)
    return b.VCPSignaal('A',101.123456,8,[],v,True,'ok',2.123456,90.123456,125.)

class Pattern(unittest.TestCase):
    def test_first_anchor_contraction_included(self):
        r=b.detect_vcp(*pattern());self.assertIsNotNone(r)
        self.assertEqual(r.n_contracties,3)
        self.assertAlmostEqual(r.contracties[0].high,100.2)
        self.assertTrue(r.breakout)
    def test_nonoverlap_and_shrink(self):
        r=b.detect_vcp(*pattern())
        self.assertTrue(all(a.end_idx < c.start_idx for a,c in zip(r.contracties,r.contracties[1:])))
        self.assertTrue(r.pct_krimpt);self.assertTrue(r.tijd_krimpt)
    def test_consecutive_peaks_do_not_share_trough(self):
        h=np.full(40,100.);h[10]=99;h[25]=98;l=np.full(40,90.);v=np.ones(40)
        r=b.extract_contracties(h,l,v,[0,10,25],[20,35])
        self.assertEqual(len(r),2);self.assertEqual([(x.start_idx,x.end_idx) for x in r],[(0,20),(25,35)])
    def test_rolling_volume_excludes_signal_day(self):
        c,h,l,v=pattern();r=b.detect_vcp(c,h,l,v)
        self.assertAlmostEqual(r.breakout_vol,v.iloc[-1]/v.iloc[-51:-1].mean())
    def test_precise_thresholds_no_early_rounding(self):
        h=np.array([100.123456,90]);l=np.array([90,90.123456]);v=np.array([100.,100.])
        r=b.extract_contracties(h,l,v,[0],[1])[0]
        self.assertEqual(r.high,100.123456)
        self.assertAlmostEqual(r.pct,(100.123456-90.123456)/100.123456*100)
    def test_invalid_volume_rejects_pattern(self):
        parts=pattern();parts[-1].iloc[-1]=math.inf
        self.assertIsNone(b.detect_vcp(*parts))
    def test_missing_confirmed_trough_not_invented(self):
        c=pd.Series(np.arange(140)+100.)
        self.assertIsNone(b.detect_vcp(c,c+1,c-1,pd.Series(np.ones(140))))
    def test_signal_retains_small_price_precision(self):
        g=history();g.loc[len(g)-1,'Close']=.123456
        v=signal().vcp;v.laatste_low=.1
        with patch.object(b,'detect_vcp',return_value=v),patch.object(b,'compute_atr_series',return_value=pd.Series([.00123456])),patch.object(b,'check_stage2',return_value=(True,'ok')):
            s=b.analyse_ticker('A',g)
        self.assertEqual(s.price,.123456);self.assertAlmostEqual(s.stop,.1-.5*.00123456)
    def test_invalid_stop_rejects_signal(self):
        v=signal().vcp;v.laatste_low=200
        with patch.object(b,'detect_vcp',return_value=v):self.assertIsNone(b.analyse_ticker('A',history()))
    def test_report_no_false_euro_risk_reward(self):
        out=b.format_bericht('X',[signal()])
        self.assertNotIn('EUR',out);self.assertNotIn('R/R:',out);self.assertIn('101.1235',out)

class History(unittest.TestCase):
    def test_invalid_ohlcv_removed(self):
        g=history();g.loc[0,'High']=0;g.loc[1,'Volume']=-1;g.loc[2,'Close']=math.inf
        self.assertEqual(len(b.clean_history(g)),337)
    def test_duplicates_sorted_timezone(self):
        g=history();g.Date=g.Date.dt.tz_localize('Europe/Brussels')
        out=b.clean_history(pd.concat([g,g.tail(1)]).sample(frac=1))
        self.assertEqual(len(out),340);self.assertEqual(out.Date.iloc[-1],pd.Timestamp('2026-11-12'))
    def test_missing_columns_empty(self):self.assertTrue(b.clean_history(history().drop(columns='Open')).empty)

class Backtest(unittest.TestCase):
    def fixture(self,days=5):
        warm=history(320);warm.Date=pd.bdate_range(end='2026-11-09',periods=320)
        future=pd.DataFrame(dict(Date=pd.bdate_range('2026-11-10',periods=days),Ticker='A',Open=110.,High=111.,Low=109.,Close=110.))
        future['Volume']=1000
        return pd.concat([warm,future],ignore_index=True)
    def run_bt(self,g,end='2026-11-20',maxdays=60):
        sig=SimpleNamespace(stop=90.,vcp=SimpleNamespace(pivot=100.,breakout=True))
        def analyse(ticker,hist):
            return sig if hist.Date.iloc[-1]==pd.Timestamp('2026-11-09') else None
        with patch.object(b,'analyse_ticker',side_effect=analyse) as mocked,patch.object(b,'MAX_HOLD_DAYS',maxdays):
            result=b.backtest_ticker('A',g,start='2026-11-09',end=end)
        return result,mocked
    def test_signal_close_executes_next_open(self):
        result,m=self.run_bt(self.fixture())
        self.assertEqual(result['open_position']['entry_date'],pd.Timestamp('2026-11-10'))
        self.assertAlmostEqual(result['open_position']['entry'],110*(1+b.SLIPPAGE_PCT))
        self.assertEqual(m.call_args_list[0].args[1].Date.max(),pd.Timestamp('2026-11-09'))
    def test_stop_checked_on_wednesday(self):
        g=self.fixture();g.loc[g.Date==pd.Timestamp('2026-11-11'),['Open','Low','Close']]=[85,84,86]
        r,_=self.run_bt(g)
        self.assertEqual(r['trades'][0]['exit_date'],'2026-11-11')
        self.assertAlmostEqual(r['trades'][0]['exit_price'],85*(1-b.SLIPPAGE_PCT))
    def test_same_bar_stop_target_stop_first(self):
        row=pd.Series(dict(Open=100,High=130,Low=80))
        self.assertEqual(b.exit_fill(row,dict(stop=90,target=120,days=1)),(90,'SL'))
    def test_gap_target_at_open(self):
        row=pd.Series(dict(Open=125,High=130,Low=121))
        self.assertEqual(b.exit_fill(row,dict(stop=90,target=120,days=1)),(125.,'TP-gap'))
    def test_holding_limit_counts_sessions(self):
        r,_=self.run_bt(self.fixture(),maxdays=2)
        self.assertEqual(r['trades'][0]['exit_date'],'2026-11-12');self.assertEqual(r['trades'][0]['reason'],'Time')
    def test_cash_and_fees_reconcile(self):
        r,_=self.run_bt(self.fixture(),maxdays=2);t=r['trades'][0]
        expected=(t['exit_price']-t['entry_price'])*t['size']-t['entry_cost']-t['exit_cost']
        self.assertAlmostEqual(t['net'],expected);self.assertAlmostEqual(r['final_value'],b.START_CAPITAL+expected)
    def test_open_position_marked_without_exit_cost(self):
        r,_=self.run_bt(self.fixture(days=1));p=r['open_position']
        self.assertAlmostEqual(r['final_value'],b.START_CAPITAL-p['entry']*p['size']-p['cost']+110*p['size'])
    def test_end_date_honoured(self):
        r,_=self.run_bt(self.fixture(),end='2026-11-09')
        self.assertIsNone(r['open_position']);self.assertEqual(r['final_value'],b.START_CAPITAL)
    def test_cash_and_risk_capped_no_forced_share(self):
        n=b.position_size(1000,100,99)
        self.assertLessEqual(n*100+b.trade_cost(n*100),1000)
        proceeds=99*(1-b.SLIPPAGE_PCT)*n
        loss=100*n+b.trade_cost(100*n)-proceeds+b.trade_cost(proceeds)
        self.assertLessEqual(loss,1000*b.RISICO_PCT_PER_TRADE)
        self.assertEqual(b.position_size(10,100,90),0)
    def test_stale_signal_not_filled(self):
        g=self.fixture(days=1);g.loc[len(g)-1,'Date']=pd.Timestamp('2026-11-18')
        r,_=self.run_bt(g);self.assertIsNone(r['open_position'])

class Live(unittest.TestCase):
    def run_bot(self,g=None,email=True,db=True,selected=True,fail=False):
        if g is None:g=history()
        with tempfile.TemporaryDirectory() as path,patch.object(b.os,'getenv',return_value=None),patch.multiple(b,
            bouw_bestandslijst=MagicMock(return_value=['x']),load_tickers_from_file=MagicMock(return_value=['A']),
            label_voor=MagicMock(return_value='X'),download_history=MagicMock(return_value=g),
            analyse_ticker=MagicMock(return_value=signal() if selected else None,side_effect=RuntimeError('analysis') if fail else None),
            log_selectie=MagicMock(return_value=db),send_email=MagicMock(return_value=email),send_telegram=MagicMock(),
            today_str=MagicMock(return_value='2026-11-13')):
            old=Path.cwd();b.os.chdir(path)
            try:
                error=None
                try:b.run_live_engine()
                except RuntimeError as e:error=e
                payload=json.loads(Path('vcp_signals.json').read_text())
                csv=pd.read_csv('vcp_signalen_2026-11-13.csv')
                result=dict(error=error,payload=payload,csv=csv,email=b.send_email.call_args,telegram=b.send_telegram.call_count)
            finally:b.os.chdir(old)
        return result
    def test_success_creates_json_csv_no_telegram(self):
        r=self.run_bot();self.assertIsNone(r['error']);self.assertEqual(r['payload']['tickers'],['A']);self.assertEqual(len(r['csv']),1);self.assertEqual(r['telegram'],0)
    def test_zero_candidates_still_reports_and_empty_schema(self):
        r=self.run_bot(selected=False);self.assertIsNone(r['error']);self.assertTrue(r['csv'].empty);self.assertIsNotNone(r['email'])
    def test_no_data_incomplete_and_failure(self):
        r=self.run_bot(g=pd.DataFrame());self.assertIsNotNone(r['error']);self.assertFalse(r['payload']['complete'])
    def test_email_failure_fails(self):self.assertIsNotNone(self.run_bot(email=False)['error'])
    def test_db_failure_fails_and_reports(self):
        r=self.run_bot(db=False);self.assertIsNotNone(r['error']);self.assertEqual(r['payload']['database']['saved'],0)
    def test_analysis_error_not_swallowed(self):
        r=self.run_bot(fail=True);self.assertIsNotNone(r['error']);self.assertEqual(len(r['payload']['coverage']['errors']),1)
    def test_current_session_excluded(self):
        g=history();g.loc[len(g)-1,'Date']=pd.Timestamp('2026-11-13')
        r=self.run_bot(g=g);self.assertIsNone(r['error'])
        self.assertLess(r['csv'].iloc[0].signal_date,'2026-11-13')

if __name__=='__main__':unittest.main()
