"""Offline tests; no external messages, database writes or downloads."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import bot_01cointegr as b


def prices(n=300):
    rng=np.random.default_rng(4)
    base=100+np.cumsum(rng.normal(0,.2,n))
    residual=np.zeros(n)
    for i in range(1,n):residual[i]=.5*residual[i-1]+rng.normal(0,.05)
    return pd.DataFrame({'A':base+residual,'B':base},index=pd.bdate_range(end='2026-11-12',periods=n))


def longframe(p=None):
    p=prices() if p is None else p
    return p.rename_axis('Date').reset_index().melt('Date',var_name='Ticker',value_name='Close')


def signal(pair='A/B',p=.001,hl=3,direction='LONG',score=10):
    a,c=pair.split('/')
    return b.PairSignaal(pair,a,c,p,hl,-2.5,1.01,1,.01,101.123456,100,direction,score,'2026-11-12')


class Tests(unittest.TestCase):
    def setUp(self):
        p=patch.object(b,'today_str',return_value='2026-11-13');p.start();self.addCleanup(p.stop)

    def test_fdr_known_stepup(self):
        self.assertEqual(b.fdr_correctie([.001,.02,.8]),[True,True,False])

    def test_fdr_no_rounding(self):
        self.assertEqual(b.fdr_correctie([.050001]),[False])

    def test_fdr_invalid(self):
        self.assertEqual(b.fdr_correctie([float('nan'),float('inf'),-.1]),[False]*3)

    def test_half_life_stationary(self):
        self.assertTrue(0<b.schat_half_life(prices().A/prices().B)<30)

    def test_half_life_short(self):
        self.assertEqual(b.schat_half_life(pd.Series([1,2,3])),float('inf'))

    def test_unrelated_missing_ticker_does_not_shorten_pair(self):
        df=prices();df['C']=np.nan;df.loc[df.index[-3:],'C']=100
        with patch.object(b,'analyseer_pair_ruw',return_value=signal()) as raw:
            out=b.selecteer_gecointegreerde_paren(['C','B','A','A'],df)
        self.assertEqual(len(out),1);self.assertEqual(len(raw.call_args.args[2]),300)

    def test_fdr_before_half_life_filter(self):
        df=prices();df['C']=df.B*1.01
        # One eligible p=.03 must fail at .05/3. Other tested pairs fail half-life.
        with patch.object(b,'analyseer_pair_ruw',side_effect=[signal(p=.03),signal('A/C',p=.9,hl=100),signal('B/C',p=.9,hl=100)]):
            self.assertEqual(b.selecteer_gecointegreerde_paren(['A','B','C'],df),[])

    def test_invalid_test_stays_in_fdr_family(self):
        df=prices();df['C']=df.B*1.01
        with patch.object(b,'analyseer_pair_ruw',side_effect=[signal(p=.03),None,None]):
            self.assertEqual(b.selecteer_gecointegreerde_paren(['A','B','C'],df),[])

    def test_pair_missing_last_session_rejected(self):
        df=prices();df.iloc[-1,0]=np.nan
        self.assertTrue(b.pair_history('A','B',df).empty)
        self.assertIsNone(b.snel_zscore('A','B',df))

    def test_raw_precision_and_date(self):
        with patch.object(b,'coint',return_value=(0,.012345678,[])),patch.object(b,'schat_half_life',return_value=2.345678):
            sig=b.analyseer_pair_ruw('A','B',prices())
        self.assertEqual(sig.p_value,.012345678);self.assertEqual(sig.half_life,2.345678)
        self.assertEqual(sig.price_a,prices().A.iloc[-1]);self.assertEqual(sig.signal_date,'2026-11-12')

    def test_neutral_and_entry_directions(self):
        df=prices();df.iloc[-1,0]=df.iloc[-1,1]*1.2
        with patch.object(b,'coint',return_value=(0,.001,[])),patch.object(b,'schat_half_life',return_value=2):
            self.assertEqual(b.analyseer_pair_ruw('A','B',df).signal,'SHORT')
            df.iloc[-1,0]=df.iloc[-1,1]*.8
            self.assertEqual(b.analyseer_pair_ruw('A','B',df).signal,'LONG')

    def test_prepare_sorts_deduplicates_timezone(self):
        df=longframe();df.Date=df.Date.dt.tz_localize('Europe/Brussels')
        df=pd.concat([df,df.tail(1)]).sample(frac=1,random_state=2)
        p,bad=b.prepare_prices(df,['A','B']);self.assertFalse(bad)
        self.assertEqual(p.shape,(300,2));self.assertEqual(p.index[-1],pd.Timestamp('2026-11-12'))

    def test_prepare_invalid_latest(self):
        for value in [0,-1,np.inf,np.nan]:
            df=longframe();df.loc[(df.Ticker=='A') & (df.Date==df.Date.max()),'Close']=value
            p,bad=b.prepare_prices(df,['A','B']);self.assertIn('A',bad);self.assertNotIn('A',p)

    def test_prepare_stale_short_missing(self):
        df=longframe(prices(50));_,bad=b.prepare_prices(df,['A','C']);self.assertEqual(len(bad),2)
        df=longframe();df.Date-=pd.Timedelta(days=20)
        _,bad=b.prepare_prices(df,['A']);self.assertIn('A',bad)

    def test_prepare_excludes_current_future(self):
        df=longframe();extra=df.tail(2).copy();extra.Date=pd.to_datetime(['2026-11-13','2026-11-16'])
        p,_=b.prepare_prices(pd.concat([df,extra]),['A','B']);self.assertEqual(p.shape,(300,2))

    def test_download_batches_and_common_fallback(self):
        with patch.object(b,'DOWNLOAD_BATCH_SIZE',1),patch.object(b,'common_download_history',return_value=longframe()) as common,patch.object(b.time,'sleep'):
            b.download_history(['B','A','A'],start='2020-01-01',end='2026-01-01')
        self.assertEqual(common.call_count,2)
        self.assertEqual(common.call_args.kwargs['start'],'2020-01-01')

    def run_bot(self, signals=None,db=True,email=True,data=None,error=None):
        with patch.multiple(b,
            bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt']),
            load_tickers_from_file=MagicMock(return_value=['A','B']),
            download_history=MagicMock(return_value=longframe() if data is None else data),
            selecteer_gecointegreerde_paren=MagicMock(return_value=[signal()] if signals is None else signals,side_effect=error),
            log_selectie=MagicMock(return_value=db),send_email=MagicMock(return_value=email)),contextlib.redirect_stdout(io.StringIO()):
            failed=None
            try:b.run_live_engine()
            except RuntimeError as exc:failed=exc
            return b.log_selectie,b.send_email,failed

    def test_live_db_date_and_email_no_telegram(self):
        db,email,error=self.run_bot();self.assertIsNone(error)
        self.assertEqual(db.call_args.kwargs['datum'],'2026-11-12');email.assert_called_once()
        self.assertNotIn('send_telegram',b.__dict__)

    def test_all_top5_saved_active_before_cap(self):
        signals=[signal(direction='NEUTRAL') for _ in range(5)]+[signal() for _ in range(7)]
        db,_,error=self.run_bot(signals);self.assertIsNone(error);self.assertEqual(db.call_count,5)

    def test_valid_empty_report(self):
        db,email,error=self.run_bot([]);self.assertIsNone(error);db.assert_not_called()
        self.assertIn('Geen actieve signalen',email.call_args.args[1])

    def test_no_data_fails_and_emails(self):
        _,email,error=self.run_bot(data=pd.DataFrame());self.assertIsNotNone(error)
        self.assertIn('[ONVOLLEDIG]',email.call_args.args[0])

    def test_database_false(self):self.assertIsNotNone(self.run_bot(db=False)[-1])
    def test_email_false(self):self.assertIsNotNone(self.run_bot(email=False)[-1])
    def test_analysis_error(self):self.assertIsNotNone(self.run_bot(error=RuntimeError('bug'))[-1])

    def test_backtest_window_warmup_no_tax_and_empty_artifacts(self):
        df=longframe();start='2026-10-20';end='2026-11-12'
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.multiple(b,BACKTEST_START=start,BACKTEST_END=end,
                    laad_exchange_tickers=MagicMock(return_value={'X':['A','B']}),
                    download_history=MagicMock(return_value=df),
                    selecteer_gecointegreerde_paren=MagicMock(return_value=[])),contextlib.redirect_stdout(io.StringIO()):
                    b.run_backtest()
                    self.assertEqual(b.download_history.call_args.kwargs['start'],'2024-10-20')
                self.assertEqual(b.TAX_RATE,0)
                self.assertTrue(pd.read_csv('backtest_results/backtest_cointegr.csv').empty)
                equity=pd.read_csv('backtest_results/cointegr_equity.csv')
                self.assertFalse(equity.empty);self.assertTrue((equity.value==b.START_CAPITAL).all())
                self.assertTrue(pd.read_csv('backtest_results/cointegr_open.csv').empty)
            finally:os.chdir(cwd)

    def simulated_backtest(self, exit_positions):
        df=longframe();start='2026-11-02';end='2026-11-12';cwd=Path.cwd()
        seen=[]
        def zscore(a,c,data):
            seen.append(data.index[-1])
            return (0 if exit_positions and len(seen)>1 else -3,1,1,.01)
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.multiple(b,BACKTEST_START=start,BACKTEST_END=end,
                    laad_exchange_tickers=MagicMock(return_value={'X':['A','B']}),
                    download_history=MagicMock(return_value=df),
                    selecteer_gecointegreerde_paren=MagicMock(return_value=[signal()])), \
                    patch.object(b,'snel_zscore',side_effect=zscore),contextlib.redirect_stdout(io.StringIO()):
                    b.run_backtest()
                return (pd.read_csv('backtest_results/backtest_cointegr.csv'),
                        pd.read_csv('backtest_results/cointegr_equity.csv'),
                        pd.read_csv('backtest_results/cointegr_open.csv'),seen)
            finally:os.chdir(cwd)

    def test_backtest_next_session_and_costs(self):
        trades,equity,positions,seen=self.simulated_backtest(True)
        self.assertEqual(trades.entry_date.iloc[0],'2026-11-02')
        self.assertLess(seen[0],pd.Timestamp(trades.entry_date.iloc[0]))
        self.assertGreater(trades.exit_date.iloc[0],trades.entry_date.iloc[0])
        self.assertEqual(trades.days.iloc[0],1)
        self.assertLess(trades.net_pnl.iloc[0],trades.gross_pnl.iloc[0])
        self.assertAlmostEqual(equity.value.iloc[-1],b.START_CAPITAL+trades.net_pnl.sum(),places=2)

    def test_backtest_open_positions_exposed(self):
        trades,equity,positions,_=self.simulated_backtest(False)
        self.assertTrue(trades.empty);self.assertEqual(len(positions),1)
        self.assertGreater(positions.entry_fees.iloc[0],0)
        self.assertNotEqual(equity.value.iloc[-1],b.START_CAPITAL)

    def test_workflow_services(self):
        import yaml
        doc=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows/run_01cointr.yml').read_text())
        job=doc['jobs']['run-bot'];steps=job['steps'];run=next(s for s in steps if s.get('name')=='Draai bot_01cointegr')
        self.assertNotIn('TELEGRAM_TOKEN',run['env']);self.assertIn('EMAIL_PASS',run['env'])
        self.assertIn('SUPABASE_DB_URL',run['env']);self.assertEqual(doc['permissions']['contents'],'read')

if __name__=='__main__':unittest.main()
