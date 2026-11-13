"""Synthetic regression tests; no real network, messages, orders or DB writes."""
import os
import sys
import json
import math
import tempfile
import unittest
import datetime as dt
from pathlib import Path
from unittest.mock import patch,MagicMock
from types import SimpleNamespace
import pandas as pd
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
for name in ('requests','yfinance','dotenv'):
    try:__import__(name)
    except ImportError:sys.modules[name]=MagicMock()
import bot_00xxxV2 as b


def history(ticker='A',n=240,end='2026-11-12'):
    close=np.linspace(80,100,n)
    return pd.DataFrame(dict(Date=pd.bdate_range(end=end,periods=n),Ticker=ticker,
        Open=close,High=close+1,Low=close-1,Close=close,Volume=1000.))


def signal(date='2026-11-09',strategy='Traag',ticker='A'):
    return b.Signal(ticker,dt.date.fromisoformat(date),strategy,'BUY','test',100,5,90,120,2)

class Temporary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=Path.cwd();os.chdir(self.temp.name)
        self.addCleanup(self.restore)
        p=patch.object(b,'today_str',return_value='2026-11-13');p.start();self.addCleanup(p.stop)
        p=patch.dict(os.environ,{},clear=True);p.start();self.addCleanup(p.stop)
    def restore(self):os.chdir(self.old);self.temp.cleanup()

class Signals(unittest.TestCase):
    def row(self,**kwargs):
        row=dict(Date=pd.Timestamp('2026-11-12'),Ticker='A',Close=100,MA20=95,MA50=90,MA200=80,RSI14=60,IBS=.8,ATR14=5,ADX14=30,Next_Open=1e6)
        row.update(kwargs);return pd.DataFrame([row])
    def generate(self,**kwargs):return b.generate_signals_for_day(self.row(**kwargs),dt.date(2026,11,12))
    def test_three_trend_strategies_unchanged(self):
        self.assertEqual({s.strategy for s in self.generate()},{'Traag','Snel','Hyper Trend'})
    def test_three_mean_reversion_strategies(self):
        result=self.generate(MA20=110,MA50=120,MA200=130,RSI14=20,IBS=.1)
        self.assertEqual({s.strategy for s in result},{'MRA Snel','MRA Traag','Hyper Scalp'})
    def test_future_open_never_part_of_signal(self):
        self.assertTrue(all(s.next_open is None for s in self.generate()))
    def test_nonfinite_close_atr_rejected(self):
        for col in ('Close','ATR14'):
            self.assertEqual(self.generate(**{col:math.inf}),[])
    def test_negative_stop_not_issued(self):self.assertEqual(self.generate(ATR14=100),[])
    def test_finite_indicators_required(self):
        self.assertFalse(any(s.strategy in ('Traag','Hyper Trend') for s in self.generate(MA50=math.inf)))
    def test_indicators_sort_input(self):
        g=history();a=b.add_indicators(g);c=b.add_indicators(g.sample(frac=1,random_state=1))
        np.testing.assert_allclose(a.MA200,c.MA200,equal_nan=True)

class History(unittest.TestCase):
    def test_invalid_ohlcv_removed(self):
        g=history();g.loc[0,'High']=0;g.loc[1,'Volume']=-1;g.loc[2,'Close']=math.inf
        self.assertEqual(len(b.clean_history(g)),237)
    def test_sorted_duplicate_local_dates(self):
        g=history();g.Date=g.Date.dt.tz_localize('Europe/Brussels')
        out=b.clean_history(pd.concat([g,g.tail(1)]).sample(frac=1))
        self.assertEqual(len(out),240);self.assertEqual(out.Date.iloc[-1],pd.Timestamp('2026-11-12'))
    def test_missing_open_not_synthesized(self):self.assertTrue(b.clean_history(history().drop(columns='Open')).empty)

class Manual(Temporary):
    def apply(self,text,portfolio=None,currency='USD'):
        Path('commands.txt').write_text(text)
        portfolio=portfolio or b.LivePortfolio(1000)
        with patch.object(b.yf,'Ticker',return_value=SimpleNamespace(info={'currency':currency})):
            b.apply_telegram_commands(portfolio,'commands.txt')
        return portfolio
    def test_buy_is_logged_and_state_restores(self):
        p=self.apply('/buy A 10 20')
        self.assertEqual(p.cash,785);self.assertEqual(b.LivePortfolio(1000).positions['A'].size,20)
        self.assertEqual(Path('commands.txt').read_text(),'')
    def test_partial_sale_respects_size_and_fee_basis(self):
        p=self.apply('/buy A 10 20')
        p=self.apply('/sell A 12 5',p)
        self.assertEqual(p.positions['A'].size,15);self.assertAlmostEqual(p.positions['A'].cost,11.25)
        self.assertAlmostEqual(p.trades[-1]['net'],60-15-50-3.75)
        self.assertEqual(p.trades[-1]['price'],12)
    def test_invalid_batch_leaves_input_and_state_unchanged(self):
        p=b.LivePortfolio(1000);text='/buy A 10 20\n/buy B nan 1'
        with self.assertRaises(ValueError):self.apply(text,p)
        self.assertEqual(p.cash,1000);self.assertFalse(p.positions)
        self.assertFalse(Path('live_state.json').exists());self.assertEqual(Path('commands.txt').read_text(),text)
    def test_duplicate_buy_rejected_without_losing_original(self):
        p=self.apply('/buy A 10 20');state=Path('live_state.json').read_bytes()
        with self.assertRaises(ValueError):self.apply('/buy A 11 1',p)
        self.assertEqual(Path('live_state.json').read_bytes(),state)
    def test_oversell_is_not_full_liquidation(self):
        p=self.apply('/buy A 10 20')
        with self.assertRaises(ValueError):self.apply('/sell A 12 21',p)
        self.assertEqual(p.positions['A'].size,20)
    def test_negative_or_zero_size_rejected(self):
        for text in ['/buy A 10 -1','/buy A 10 0','/buy A -1 1']:
            with self.assertRaises(ValueError):self.apply(text)
    def test_currency_mismatch_rejected(self):
        with self.assertRaises(ValueError):self.apply('/buy A 10 20',currency='EUR')
        self.assertFalse(Path('live_state.json').exists())
    def test_cash_limit_enforced(self):
        with self.assertRaises(ValueError):self.apply('/buy A 100 20')
    def test_replayed_file_not_executed_twice(self):
        p=self.apply('/buy A 10 20');cash=p.cash
        p=self.apply('/buy A 10 20',p)
        self.assertEqual(p.cash,cash);self.assertEqual(len(p.trades),1)
    def test_replay_across_midnight_not_repeated(self):
        text='/buy A 10 20';p=self.apply(text)
        with patch.object(b,'today_str',return_value='2026-11-14'):
            p=self.apply(text,p)
        self.assertEqual(p.cash,785);self.assertEqual(len(p.trades),1)
    def test_commit_failure_keeps_commands(self):
        text='/buy A 10 20';p=b.LivePortfolio(1000)
        with patch.object(b,'atomic_json',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.apply(text,p)
        self.assertEqual(Path('commands.txt').read_text(),text);self.assertEqual(p.cash,1000)
    def test_projection_failure_replay_repairs_without_repeating(self):
        p=b.LivePortfolio(1000);text='/buy A 10 20'
        with patch.object(pd.DataFrame,'to_csv',side_effect=OSError('projection')):
            with self.assertRaises(OSError):self.apply(text,p)
        self.assertTrue(Path('live_state.json').exists())
        p=self.apply(text,p)
        self.assertEqual(p.cash,785);self.assertEqual(len(p.trades),1)
        self.assertEqual(len(pd.read_csv('trades_live.csv')),1)
    def test_legacy_csv_not_silently_replaced(self):
        Path('positions_live.csv').write_text('old state')
        with self.assertRaises(ValueError):b.LivePortfolio(1000)
    def test_missing_price_not_marked_at_entry_as_current(self):
        p=self.apply('/buy A 10 20')
        with self.assertRaises(ValueError):p.current_total_value({})
        self.assertTrue(pd.isna(pd.read_csv('portfolio_live.csv').total.iloc[0]))

class Backtest(unittest.TestCase):
    def fixture(self,n=5):
        g=history(n=200,end='2026-11-09');g[['Open','Close']]=100.;g.High=101.;g.Low=99.
        future=pd.DataFrame(dict(Date=pd.bdate_range('2026-11-10',periods=n),Ticker='A',Open=110.,High=111.,Low=109.,Close=110.,Volume=1000.))
        return pd.concat([g,future],ignore_index=True)
    def run_bt(self,g,limit=20):
        def generate(frame,date):return [signal()] if date==dt.date(2026,11,9) else []
        with patch.object(b,'generate_signals_for_day',side_effect=generate),patch.object(b,'MAX_HOLD_DAYS',limit):
            return b.backtest_ticker('A',g,start='2026-11-09',end='2026-11-30')
    def test_actual_next_open_entry_date_and_rebased_stops(self):
        bt=self.run_bt(self.fixture(n=1));p=bt.positions['A']
        self.assertEqual(p.entry_date,'2026-11-10');self.assertAlmostEqual(p.entry_price,110*(1+b.SLIPPAGE_PCT))
        self.assertAlmostEqual(p.sl,p.entry_price-10)
    def test_next_open_missing_never_falls_back_to_close(self):
        bt=b.BacktestPortfolio(1000);self.assertFalse(bt.open_position(signal(),{'A':100}))
    def test_stop_checked_on_next_day_gap(self):
        g=self.fixture();g.loc[g.Date==pd.Timestamp('2026-11-11'),['Open','Low','Close']]=[90,89,91]
        bt=self.run_bt(g);t=bt.closed_trades[0]
        self.assertEqual(t['exit_date'],'2026-11-11');self.assertAlmostEqual(t['exit_price'],90*(1-b.SLIPPAGE_PCT))
    def test_both_stop_target_stop_first(self):
        p=b.LivePosition('A','Traag','2026-11-09',100,1,15,90,120)
        self.assertEqual(b.intraday_exit(pd.Series(dict(Open=100,High=130,Low=80)),p),(90,'SL'))
    def test_time_exit_is_next_open(self):
        bt=self.run_bt(self.fixture(),limit=2)
        self.assertEqual(bt.closed_trades[0]['exit_date'],'2026-11-12')
        self.assertEqual(bt.closed_trades[0]['reason'],'Time exit')
    def test_close_rule_exits_following_session(self):
        g=self.fixture();g.loc[g.Date==pd.Timestamp('2026-11-11'),'Close']=104
        g.loc[g.Date==pd.Timestamp('2026-11-11'),'Low']=103
        bt=self.run_bt(g)
        # Explicitly exercise close-rule queue without relying on an incidental MA threshold.
        with patch.object(b,'close_exit_reason',return_value='Trend exit MA20'):
            bt=self.run_bt(self.fixture())
        self.assertEqual(bt.closed_trades[0]['exit_date'],'2026-11-11')
    def test_fees_cash_and_pnl_reconcile(self):
        bt=self.run_bt(self.fixture(),limit=2);t=bt.closed_trades[0]
        pnl=(t['exit_price']-t['entry_price'])*t['size']-t['entry_cost']-t['exit_cost']
        self.assertAlmostEqual(t['net'],pnl);self.assertAlmostEqual(bt.cash,b.START_CAPITAL+pnl)
    def test_open_position_has_no_fictitious_exit_fee(self):
        bt=self.run_bt(self.fixture(n=1));p=bt.positions['A']
        self.assertAlmostEqual(bt.daily_snapshots[-1]['total'],b.START_CAPITAL-p.entry_price*p.size-p.cost+110*p.size)
    def test_cash_reserve_and_risk_include_fees(self):
        n=b.affordable_size(1000,1000,100,99)
        self.assertLessEqual(100*n+b.trade_cost(100*n),900)
        proceeds=99*(1-b.SLIPPAGE_PCT)*n
        self.assertLessEqual(100*n+b.trade_cost(100*n)-proceeds+b.trade_cost(proceeds),50)
        self.assertEqual(b.affordable_size(10,10,100,90),0)
    def test_no_history_state_loaded_by_backtest(self):
        with patch.object(b.LivePortfolio,'load_state',side_effect=AssertionError('loaded')):
            self.run_bt(self.fixture())
    def test_stats_include_initial_loss_in_drawdown(self):
        bt=b.BacktestPortfolio(b.START_CAPITAL)
        bt.daily_snapshots=[dict(date='2026-11-09',total=49000),dict(date='2026-11-10',total=49500)]
        s=b.account_stats(bt);self.assertEqual(s['max_drawdown_pct'],-2)
        self.assertIsNone(s['cagr_pct']);self.assertIsNone(s['profit_factor'])
    def test_future_bars_cannot_change_earlier_entry(self):
        g=self.fixture();a=self.run_bt(g)
        g.loc[len(g)-1,['Open','High','Low','Close']]=[200,201,199,200]
        c=self.run_bt(g)
        self.assertEqual(a.daily_snapshots[:3],c.daily_snapshots[:3])

class Live(Temporary):
    def run_bot(self,g=None,db=True,email=True,selected=True,fail=False):
        if g is None:g=history()
        def generate(frame,date):return [signal(date=date.isoformat())] if selected else []
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['x']),
            load_tickers_from_file=MagicMock(return_value=['A']),label_voor=MagicMock(return_value='X'),
            download_history=MagicMock(return_value=g),generate_signals_for_day=MagicMock(side_effect=RuntimeError('analysis') if fail else generate),
            log_selectie=MagicMock(return_value=db),send_email=MagicMock(return_value=email),send_telegram=MagicMock()):
            error=None
            try:b.run_live_engine()
            except RuntimeError as e:error=e
            return dict(error=error,payload=json.loads(Path('xxxV2_scan.json').read_text()),
                csv=pd.read_csv('xxxV2_signalen_2026-11-13.csv'),email=b.send_email.call_args,telegram=b.send_telegram.call_count)
    def test_success_database_email_telegram_off(self):
        r=self.run_bot();self.assertIsNone(r['error']);self.assertEqual(r['payload']['database']['saved'],1);self.assertEqual(r['telegram'],0)
    def test_scan_does_not_touch_existing_portfolio(self):
        b.LivePortfolio(1000).save_state('2026-11-12',{})
        before={file:Path(file).read_bytes() for file in (b.LIVE_STATE_FILE,b.LIVE_POSITIONS_FILE,b.LIVE_PORTFOLIO_FILE,b.LIVE_TRADES_FILE)}
        r=self.run_bot();self.assertIsNone(r['error'])
        for file in (b.LIVE_STATE_FILE,b.LIVE_POSITIONS_FILE,b.LIVE_PORTFOLIO_FILE,b.LIVE_TRADES_FILE):self.assertEqual(Path(file).read_bytes(),before[file])
    def test_no_candidates_still_emails(self):
        r=self.run_bot(selected=False);self.assertIsNone(r['error']);self.assertTrue(r['csv'].empty);self.assertIsNotNone(r['email'])
    def test_no_data_fails_after_report(self):
        r=self.run_bot(g=pd.DataFrame());self.assertIsNotNone(r['error']);self.assertFalse(r['payload']['complete'])
    def test_email_failure_fails(self):self.assertIsNotNone(self.run_bot(email=False)['error'])
    def test_database_failure_fails(self):self.assertIsNotNone(self.run_bot(db=False)['error'])
    def test_analysis_error_visible(self):
        r=self.run_bot(fail=True);self.assertIsNotNone(r['error']);self.assertIn('analysis',r['payload']['errors'][0])
    def test_each_ticker_uses_own_completed_session(self):
        g=history();g.loc[len(g)-1,'Date']=pd.Timestamp('2026-11-13')
        r=self.run_bot(g);self.assertLess(r['csv'].signal_date.iloc[0],'2026-11-13')
    def test_repeat_exit_advice_uses_sessions_without_mutating_state(self):
        p=b.LivePortfolio(1000,load=False)
        p.positions['A']=b.LivePosition('A','MANUAL','2026-10-01',90,1,15,None,None)
        g=b.add_indicators(history())
        first=b.generate_exit_signals(p,g)
        second=b.generate_exit_signals(p,g)
        self.assertEqual(first,second);self.assertEqual(p.positions['A'].days_open,0)
        self.assertIn('Time exit',first[0].reason)

    def test_stale_ticker_skipped(self):
        g=history(end='2026-10-01');r=self.run_bot(g)
        self.assertIsNotNone(r['error']);self.assertIn('ouder',r['payload']['skipped']['A'])

if __name__=='__main__':unittest.main()
