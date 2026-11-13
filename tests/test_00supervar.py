"""Offline regression tests; all delivery and database calls are mocked."""
import sys
import math
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import pandas as pd
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ('yfinance','requests','dotenv'):
    try: __import__(name)
    except ImportError: sys.modules[name] = MagicMock()
import bot_00supervar as b

def history(ticker='A', n=280, growth=1):
    close = np.linspace(50, 50+growth*50, n)
    return pd.DataFrame(dict(Date=pd.bdate_range(end='2026-11-12', periods=n), Ticker=ticker,
        Open=close, Close=close, High=close+1, Low=close-1, Volume=np.full(n, 1000.)))

def signal(ticker='A', score=100):
    return b.SuperSignaal(ticker, .123456, 3, .13, .115, 99, 2, 50, score)

class Prepare(unittest.TestCase):
    def setUp(self):
        p=patch.object(b,'today_str',return_value='2026-11-13');p.start();self.addCleanup(p.stop)
    def test_valid_sorted_unique(self):
        g=history(); g=pd.concat([g,g.tail(1)]).sample(frac=1, random_state=1)
        out,bad=b.prepare_universe(g,['A','A'])
        self.assertEqual(len(out),280); self.assertFalse(bad)
        self.assertTrue(out.Date.is_monotonic_increasing)
    def test_missing_ticker_reported(self):
        _,bad=b.prepare_universe(history(),['A','B']);self.assertIn('B',bad)
    def test_no_data_reported(self):
        out,bad=b.prepare_universe(pd.DataFrame(),['A']);self.assertTrue(out.empty);self.assertIn('A',bad)
    def test_short_history_rejected(self):
        _,bad=b.prepare_universe(history(n=252),['A']);self.assertIn('253',bad['A'])
    def test_stale_history_rejected(self):
        g=history();g.Date-=pd.Timedelta(days=15)
        _,bad=b.prepare_universe(g,['A']);self.assertIn('ouder',bad['A'])
    def test_future_and_current_session_excluded(self):
        g=history();today=g.tail(1).copy();today.Date=pd.Timestamp('2026-11-13')
        future=today.copy();future.Date=pd.Timestamp('2026-11-16')
        out,_=b.prepare_universe(pd.concat([g,today,future]),['A'])
        self.assertEqual(out.Date.max(),pd.Timestamp('2026-11-12'))
    def test_invalid_ohlcv_rows_removed(self):
        for col,value in [('High',0),('Close',np.inf),('Volume',-1),('Open',np.nan)]:
            with self.subTest(col=col):
                g=history();g.loc[10,col]=value
                out,_=b.prepare_universe(g,['A']);self.assertEqual(len(out),279)
    def test_volume_zero_universe_rejected(self):
        g=history();g.Volume=0
        _,bad=b.prepare_universe(g,['A']);self.assertIn('handelsvolume',bad['A'])
    def test_timezone_is_local_session_date(self):
        g=history();g.Date=g.Date.dt.tz_localize('Europe/Brussels')
        out,_=b.prepare_universe(g,['A']);self.assertEqual(out.Date.iloc[-1],pd.Timestamp('2026-11-12'))

class Indicators(unittest.TestCase):
    def test_rs_has_top_99_bottom_1(self):
        df=pd.concat([history('A',growth=1),history('B',growth=2)])
        scores=b.compute_rs_ratings(df);self.assertEqual(scores['B'],99);self.assertEqual(scores['A'],1)
    def test_rs_equal_returns_equal_neutral(self):
        scores=b.compute_rs_ratings(pd.concat([history('A'),history('B')]))
        self.assertEqual(scores,{'A':50,'B':50})
    def test_single_ticker_not_forced_top(self):
        self.assertEqual(b.compute_rs_ratings(history()),{'A':50})
    def test_rs_requires_252_intervals(self):
        self.assertEqual(b.compute_rs_ratings(history(n=252)),{})
    def test_nonfinite_rs_price_rejected(self):
        g=history();g.loc[g.index[-64],'Close']=np.inf
        self.assertEqual(b.compute_rs_ratings(g),{})
    def test_high_low_and_lagged_volume(self):
        g=history();g.loc[g.index[-1],'Volume']=100000
        out=b.add_indicators(g.sample(frac=1),{'A':99})
        self.assertEqual(out.iloc[-1].VolMA20,1000)
        self.assertEqual(out.iloc[-1].High52w,g.High.tail(252).max())
        self.assertEqual(out.iloc[-1].Low52w,g.Low.tail(252).min())
    def test_pivot_prior_20_highs(self):
        g=b.add_indicators(history(),{'A':99})
        g.loc[g.index[-21:-1],'High']=105
        g.loc[g.index[-1],['Close','High','Volume']]=[110,112,2000]
        _,details,pivot,stop=b.detect_vcp(g)
        self.assertEqual(pivot,105);self.assertTrue(any('✓ Pivot breakout' in d for d in details))
        self.assertLess(stop,110)
    def test_no_false_breakout_above_close_below_high(self):
        g=b.add_indicators(history(),{'A':99})
        g.loc[g.index[-21:-1],'High']=120
        g.loc[g.index[-1],['Close','High','Volume']]=[110,112,2000]
        _,details,_,_=b.detect_vcp(g)
        self.assertTrue(any('✗ Geen pivot breakout' in d for d in details))
    def test_infinite_trend_rejected(self):
        row=pd.Series(dict(Close=np.inf,MA50=90,MA150=80,MA200=70,MA200_slope=1,High52w=100,Low52w=40,RS=99))
        self.assertFalse(b.check_trend_template(row)[0])
    def test_precision_preserved(self):
        g=b.add_indicators(history(),{'A':99});g.loc[g.index[-1],'Close']=100.123456
        with patch.object(b,'check_trend_template',return_value=(True,[])), patch.object(b,'detect_vcp',return_value=(3,[],110.123456,95.123456)):
            s=b.analyse_ticker('A',g,0)
        self.assertEqual(s.price,100.123456);self.assertEqual(s.stop,95.123456)
    def test_stop_at_or_above_price_rejected(self):
        g=b.add_indicators(history(),{'A':99})
        with patch.object(b,'check_trend_template',return_value=(True,[])), patch.object(b,'detect_vcp',return_value=(3,[],110,100)):
            self.assertIsNone(b.analyse_ticker('A',g,0))
    def test_report_does_not_invent_euro_position(self):
        report=b.format_bericht('X',signal(),50000)
        self.assertNotIn('EUR',report);self.assertNotIn('R/R:',report)
        self.assertIn('0.1235',report);self.assertIn('geen koersdoel',report)

class Live(unittest.TestCase):
    def run_bot(self, data=None, db=True, email=True, candidate=True, error=False):
        if data is None: data=history()
        with patch.multiple(b, REEKS_START=41, REEKS_EINDE=41,
            load_tickers_from_file=MagicMock(return_value=['A']),
            download_history=MagicMock(return_value=data),
            analyse_ticker=MagicMock(side_effect=RuntimeError('analyse test') if error else None,return_value=signal() if candidate else None),
            send_email=MagicMock(return_value=email),send_telegram=MagicMock(),log_selectie=MagicMock(return_value=db),
            today_str=MagicMock(return_value='2026-11-13')), patch.dict(b.os.environ,{},clear=True):
            failure=None
            try:b.run_live_engine('europa')
            except RuntimeError as e:failure=e
            return {k:getattr(b,k) for k in ['send_email','send_telegram','log_selectie']},failure
    def test_success_telegram_off(self):
        mocks,error=self.run_bot();self.assertIsNone(error)
        mocks['send_telegram'].assert_not_called();self.assertIn('1/1 selecties',mocks['send_email'].call_args.args[1])
    def test_no_candidates_is_not_error(self):
        mocks,error=self.run_bot(candidate=False);self.assertIsNone(error);mocks['send_email'].assert_called_once()
    def test_no_data_report_and_failure(self):
        mocks,error=self.run_bot(data=pd.DataFrame());self.assertIsNotNone(error)
        self.assertIn('[ONVOLLEDIG]',mocks['send_email'].call_args.args[0])
    def test_failed_database_fails_run(self):
        _,error=self.run_bot(db=False);self.assertIsNotNone(error)
    def test_failed_email_fails_run(self):
        _,error=self.run_bot(email=False);self.assertIsNotNone(error)
    def test_analysis_error_is_not_no_candidate(self):
        mocks,error=self.run_bot(error=True);self.assertIsNotNone(error)
        self.assertIn('analyse test',mocks['send_email'].call_args.args[1])

if __name__=='__main__':unittest.main()
