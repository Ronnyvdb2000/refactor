"""Offline Marktsentiment regressions; external calls are mocked."""
import contextlib
import io
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch
import numpy as np
import pandas as pd
import requests
import bot_01marktsent as b


def history(ticker='A',n=300):
    close=100+np.linspace(0,15,n)+np.sin(np.arange(n)/5)
    return pd.DataFrame(dict(Date=pd.bdate_range(end='2026-11-13',periods=n),Ticker=ticker,
        Open=close,High=close+2,Low=close-2,Close=close,Volume=np.full(n,1000.)))


def signal(ticker='A',tech=4,price=100,stop=90,date='2026-11-13'):
    return b.MSSignaal(ticker,price,60,'Verse crossover',tech,None,0,tech,5,stop,date)


def article(title='Company profits jump',date='2026-11-13T12:00:00Z',url='https://example.com/a'):
    return dict(title=title,description='Strong growth and excellent results',publishedAt=date,url=url)


class Tests(unittest.TestCase):
    def setUp(self):
        p=patch.object(b,'today_str',return_value='2026-11-14');p.start();self.addCleanup(p.stop)
        b._newsapi_calls_used=0;b._newsapi_rate_limited=False;b._newsapi_blocked_reason="";b._news_status.clear();b._company_name_cache.clear()
        p=patch.object(b,'NEWSAPI_KEY','test-key');p.start();self.addCleanup(p.stop)

    def news(self,articles=None,status=200,payload=None):
        response=MagicMock(status_code=status)
        response.json.return_value=payload if payload is not None else {'status':'ok','articles':articles or []}
        with patch.object(b.requests,'get',return_value=response) as get:
            result=b.fetch_news_sentiment('A','Company',asof='2026-11-14T06:00:00Z')
            return result,get

    def test_news_two_valid_unique(self):
        (score,n),get=self.news([article(),article('Better growth',url='https://example.com/b')])
        self.assertEqual(n,2);self.assertGreater(score,0)
        self.assertEqual(get.call_args.kwargs['headers'],{'X-Api-Key':'test-key'})
        self.assertNotIn('apiKey',get.call_args.kwargs['params'])

    def test_minimum_applies_after_empty_and_removed_filter(self):
        (score,n),_=self.news([article(),article('[Removed]',url='https://example.com/b'),dict(title=None,description=None)])
        self.assertIsNone(score);self.assertEqual(n,1)

    def test_duplicate_title_or_url_not_counted_twice(self):
        (score,n),_=self.news([article(),article(url='https://example.com/b'),article('Other title')])
        self.assertIsNone(score);self.assertEqual(n,1)

    def test_old_future_and_invalid_date_not_counted(self):
        (score,n),_=self.news([article(date='2026-10-01T00:00:00Z'),article(date='2026-11-15T00:00:00Z'),article(date='bad')])
        self.assertIsNone(score);self.assertEqual(n,0)

    def test_unusable_vader_values_not_counted(self):
        with patch.object(b._vader,'polarity_scores',return_value={'compound':np.inf}):
            (score,n),_=self.news([article(),article('Other',url='b')])
        self.assertIsNone(score);self.assertEqual(n,0)

    def test_missing_key_no_request(self):
        with patch.object(b,'NEWSAPI_KEY',''):
            (score,n),get=self.news()
        get.assert_not_called();self.assertIsNone(score);self.assertIn('ontbreekt',b._news_status['A'])

    def test_budget_enforced(self):
        with patch.object(b,'NEWSAPI_DAILY_BUDGET',1):
            self.news()
            _,get=self.news();get.assert_not_called()
        self.assertEqual(b._newsapi_calls_used,1)

    def test_rate_limit_stops_following_requests(self):
        self.news(status=429)
        _,get=self.news();get.assert_not_called();self.assertTrue(b._newsapi_rate_limited)

    def test_http_error_unknown(self):
        (score,n),_=self.news(status=401);self.assertIsNone(score);self.assertEqual(n,0)
        self.assertIn('401',b._news_status['A'])

    def test_bad_credentials_stop_further_requests(self):
        self.news(status=401)
        _,get=self.news();get.assert_not_called()
        self.assertIn('401',b._news_status['A'])

    def test_invalid_response_unknown(self):
        (score,n),_=self.news(payload={'status':'error','articles':[]})
        self.assertIsNone(score);self.assertIn('ongeldig',b._news_status['A'])

    def test_network_error_no_secret_logged(self):
        out=io.StringIO()
        with patch.object(b.requests,'get',side_effect=requests.ConnectionError('test-key')),contextlib.redirect_stdout(out):
            score,n=b.fetch_news_sentiment('A')
        self.assertIsNone(score);self.assertNotIn('test-key',out.getvalue())
        self.assertNotIn('test-key',b._news_status['A'])

    def test_sentiment_bonus_thresholds(self):
        self.assertEqual(b.sentiment_bonus(.14),0);self.assertEqual(b.sentiment_bonus(.2),.2)
        self.assertEqual(b.sentiment_bonus(-.2),-.2);self.assertEqual(b.sentiment_bonus(None),0)
        self.assertEqual(b.sentiment_bonus(np.nan),0)

    def test_positive_and_negative_news_keep_original_scoring(self):
        b._news_status['A']='beschikbaar'
        with patch.object(b,'resolve_company_name',return_value='Company'), \
             patch.object(b,'fetch_news_sentiment',return_value=(-.5,2)):
            updated=b.add_sentiment(signal())
        self.assertEqual(updated.score,3.5)
        with patch.object(b,'resolve_company_name',return_value='Company'), \
             patch.object(b,'fetch_news_sentiment',return_value=(.5,2)):
            updated=b.add_sentiment(signal(tech=3))
        self.assertEqual(updated.score,3.5)

    def test_prepare_sort_duplicates_local_timezone(self):
        h=history();h.Date=h.Date.dt.tz_localize('Europe/Brussels')
        h=pd.concat([h,h.tail(1)]).sample(frac=1,random_state=2)
        p=b.prepare_history(h);self.assertEqual(len(p),300);self.assertEqual(p.Date.iloc[-1],pd.Timestamp('2026-11-13'))

    def test_prepare_excludes_current_future(self):
        h=history();new=h.tail(2).copy();new.Date=pd.to_datetime(['2026-11-14','2026-11-16'])
        self.assertEqual(len(b.prepare_history(pd.concat([h,new]))),300)

    def test_prepare_requires_actual_ohlcv(self):
        for col in ['Open','High','Low','Volume']:
            with self.assertRaises(b.DataUnavailable):b.prepare_history(history().drop(columns=col))

    def test_prepare_invalid_last_not_replaced(self):
        for col,value in [('Close',np.inf),('Low',0),('Volume',-1),('High',1)]:
            h=history();h.loc[len(h)-1,col]=value
            with self.assertRaises(b.DataUnavailable):b.prepare_history(h)

    def test_prepare_removes_old_bad_rows_with_count(self):
        h=history();h.loc[0,'Low']=np.nan
        p=b.prepare_history(h);self.assertEqual(len(p),299);self.assertEqual(p.attrs['discarded_rows'],1)

    def test_prepare_short_stale_zero_volume(self):
        stale=history();stale.Date-=pd.Timedelta(days=10)
        zero=history();zero.Volume=0
        for h in [history(n=209),stale,zero,pd.DataFrame()]:
            with self.assertRaises(b.DataUnavailable):b.prepare_history(h)

    def technical(self,rsi=60,atr=2,cross=True,close=110):
        h=history();h.loc[h.index[-1],'Close']=close
        n=len(h);macd=np.ones(n)
        if cross:macd[-3]=-1
        with patch.object(b,'compute_rsi_series',return_value=pd.Series(np.full(n,rsi))), \
             patch.object(b,'compute_atr_series',return_value=pd.Series(np.full(n,atr))), \
             patch.object(b,'compute_macd_series',return_value=(pd.Series(macd),pd.Series(np.zeros(n)),pd.Series(macd))):
            return b.analyse_ticker('A',h)

    def test_four_original_technical_points(self):
        s=self.technical(close=120);self.assertEqual(s.tech_score,4)
        self.assertEqual(s.stop,116);self.assertEqual(s.signal_date,'2026-11-13')

    def test_original_below_sma50_rule_preserved(self):
        s=self.technical(rsi=40,cross=False,close=90)
        self.assertEqual(s.tech_score,1)  # Only capped extension; negative extension still passes.

    def test_no_fresh_cross_no_point(self):
        self.assertEqual(self.technical(close=120,cross=False).tech_score,3)

    def test_bad_stop_and_indicators_rejected(self):
        for rsi,atr in [(np.inf,2),(60,np.nan),(60,100),(60,0)]:
            with self.assertRaises(b.DataUnavailable):self.technical(rsi=rsi,atr=atr)

    def test_daily_rsi_label_not_monthly(self):
        report=b.format_bericht('X',[signal()])
        self.assertIn('RSI(14) dagdata',report);self.assertIn('bronkoerseenheden',report)
        self.assertNotIn('EUR100',report)

    def live(self,techs=None,db=True,email=True,data=None,error=None):
        techs=techs or {'A':4}
        frames=pd.concat([history(t) for t in techs],ignore_index=True) if data is None else data
        def analyse(ticker,group,**kwargs):
            if error:raise error
            return signal(ticker,tech=techs[ticker])
        def enrich(sig,asof=None):
            return b.replace(sig,news_status='nieuws onbekend',sentiment_score=None,score=sig.tech_score)
        with patch.multiple(b,bouw_bestandslijst=MagicMock(return_value=['tickers_041x.txt','tickers_042x.txt']),
            load_tickers_from_file=MagicMock(return_value=list(techs)),download_history=MagicMock(return_value=frames),
            analyse_ticker=MagicMock(side_effect=analyse),add_sentiment=MagicMock(side_effect=enrich),
            log_selectie=MagicMock(return_value=db),send_email=MagicMock(return_value=email)),contextlib.redirect_stdout(io.StringIO()):
            failure=None
            try:b.run_live_engine()
            except RuntimeError as exc:failure=exc
            return b.analyse_ticker,b.add_sentiment,b.log_selectie,b.send_email,failure

    def test_live_deduplicates_technical_and_news(self):
        tech,news,db,email,error=self.live();self.assertIsNone(error)
        tech.assert_called_once();news.assert_called_once();self.assertEqual(db.call_count,2)
        self.assertNotIn('send_telegram',b.__dict__)
        self.assertIn('2/2 selecties',email.call_args.args[1])
        self.assertEqual(db.call_args.kwargs['datum'],'2026-11-13')

    def test_impossible_tech2_does_not_spend_news_budget(self):
        _,news,db,email,error=self.live({'A':2,'B':3,'C':4});self.assertIsNone(error)
        self.assertEqual([c.args[0].ticker for c in news.call_args_list],['B','C'])
        self.assertEqual(db.call_count,2)  # Only C, per two exchanges.

    def test_news_missing_reports_incomplete(self):
        _,_,_,email,error=self.live();self.assertIsNone(error)
        self.assertIn('[ONVOLLEDIG]',email.call_args.args[0]);self.assertIn('nieuws onbekend',email.call_args.args[1])

    def test_valid_empty_selection_still_emails(self):
        _,_,db,email,error=self.live({'A':1});self.assertIsNone(error)
        db.assert_not_called();email.assert_called_once()

    def test_empty_data_fails_and_emails(self):
        *_,email,error=self.live(data=pd.DataFrame());self.assertIsNotNone(error);email.assert_called_once()

    def test_database_false_fails(self):self.assertIsNotNone(self.live(db=False)[-1])
    def test_email_false_fails(self):self.assertIsNotNone(self.live(email=False)[-1])
    def test_analysis_error_fails(self):self.assertIsNotNone(self.live(error=RuntimeError('test'))[-1])

    def test_top15_is_same_storage_and_report_limit(self):
        _,_,db,email,error=self.live({f'T{i:02}':4 for i in range(17)})
        self.assertIsNone(error);self.assertEqual(db.call_count,30)
        self.assertIn('17 kandidaten, 15 topselecties',email.call_args.args[1])

    def test_run_resets_budget(self):
        b._newsapi_calls_used=95;b._newsapi_rate_limited=True
        self.live();self.assertEqual(b._newsapi_calls_used,0);self.assertFalse(b._newsapi_rate_limited)

    def test_position_cash_and_stop_risk_including_fees(self):
        n=b.position_size(50000,101,90)
        self.assertGreater(n,0)
        self.assertLessEqual(n*101+b.trade_cost(n*101),50000)
        loss=n*(101-90*(1-b.SLIPPAGE_PCT))+b.trade_cost(n*101)+b.trade_cost(n*90*(1-b.SLIPPAGE_PCT))
        self.assertLessEqual(loss,2500)
        self.assertEqual(b.position_size(1,100,90),0)
        self.assertEqual(b.position_size(50000,90,100),0)

    def backtest(self,modify=None,n=80,mode='always'):
        dates=pd.bdate_range(end='2026-09-30',periods=210+n)
        h=pd.DataFrame(dict(Date=dates,Ticker='A',Open=100.,High=102.,Low=98.,Close=100.,Volume=1000.))
        start=dates[210]
        if modify:modify(h,210)
        seen=[]
        def technical(ticker,g,met_sentiment=False):
            seen.append(g.Date.iloc[-1]);self.assertFalse(met_sentiment)
            return signal(ticker,price=100,stop=90,date=g.Date.iloc[-1].date().isoformat(),tech=4 if mode=='always' else 0)
        with patch.object(b,'analyse_ticker',side_effect=technical):
            out=b.backtest_ticker('A',h,start,dates[-1])
        return out,h,start,seen

    def test_backtest_next_open_and_closed_signal(self):
        def modify(h,i):h.loc[i+1,['Open','High']]=[105,106]
        (summary,trades,curve),h,start,seen=self.backtest(modify)
        self.assertEqual(trades[0]['entry_date'],h.Date.iloc[211].date().isoformat())
        self.assertAlmostEqual(trades[0]['entry_price'],105*(1+b.SLIPPAGE_PCT))
        self.assertEqual(seen[0],start)

    def test_backtest_daily_stop_between_monthly_scans(self):
        def modify(h,i):h.loc[i+3,'Low']=89
        (_,trades,_),h,_,_=self.backtest(modify,n=10)
        self.assertEqual(trades[0]['exit_date'],h.Date.iloc[213].date().isoformat())
        self.assertAlmostEqual(trades[0]['exit_price'],90*(1-b.SLIPPAGE_PCT))
        self.assertEqual(trades[0]['sessions_held'],3)

    def test_backtest_gap_stop_uses_open(self):
        def modify(h,i):h.loc[i+3,['Open','Low','Close']]=[80,79,81]
        (_,trades,_),_,_,_=self.backtest(modify,n=10)
        self.assertAlmostEqual(trades[0]['exit_price'],80*(1-b.SLIPPAGE_PCT))

    def test_backtest_stop_on_entry_day(self):
        def modify(h,i):h.loc[i+1,'Low']=89
        (_,trades,_),_,_,_=self.backtest(modify,n=10)
        self.assertEqual(trades[0]['sessions_held'],1)

    def test_backtest_entry_below_stop_is_skipped(self):
        def modify(h,i):h.loc[i+1,['Open','Low','Close']]=[85,84,86]
        (summary,trades,_),_,_,_=self.backtest(modify,n=3)
        self.assertFalse(trades);self.assertEqual(summary['open_positions'],0)

    def test_backtest_time_exit_after_sixty_complete_sessions(self):
        (_,trades,_),h,_,_=self.backtest(n=65)
        self.assertEqual(trades[0]['exit_date'],h.Date.iloc[271].date().isoformat())
        self.assertIn('60 afgeronde',trades[0]['reason'])

    def test_backtest_fees_reconcile_no_tax(self):
        def modify(h,i):h.loc[i+3,'Low']=89
        (summary,trades,_),_,_,_=self.backtest(modify,n=10)
        self.assertAlmostEqual(summary['final_value'],50000+sum(t['pnl'] for t in trades))
        self.assertNotIn('tax',trades[0]);self.assertGreater(trades[0]['entry_fee'],0)
        self.assertGreater(summary['max_drawdown_pct'],0)

    def test_backtest_open_valuation_no_fictitious_exit_fee(self):
        (summary,trades,_),_,_,_=self.backtest(n=10)
        self.assertFalse(trades);self.assertEqual(summary['open_positions'],1)
        expected=50000-summary['open_entry_price']*summary['open_size']-summary['open_entry_fee']+100*summary['open_size']
        self.assertAlmostEqual(summary['final_value'],expected)
        self.assertTrue(math.isnan(summary['profit_factor']))

    def test_backtest_initial_value_and_empty_trades(self):
        (summary,trades,curve),_,_,_=self.backtest(mode='none')
        self.assertFalse(trades);self.assertEqual(summary['final_value'],50000)
        self.assertEqual(curve[0]['value'],50000)

    def test_backtest_requires_warmup_before_start(self):
        h=history(n=210)
        with self.assertRaises(b.DataUnavailable):b.backtest_ticker('A',h,h.Date.iloc[0],h.Date.iloc[-1])

    def test_backtest_runner_dates_csv_and_no_news(self):
        h=history(n=300);start=h.Date.iloc[220].date().isoformat();end=h.Date.iloc[-1].date().isoformat()
        cwd=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.multiple(b,BACKTEST_START=start,BACKTEST_END=end,
                    bouw_bestandslijst=MagicMock(return_value=['x']),load_tickers_from_file=MagicMock(return_value=['A']),
                    download_history=MagicMock(return_value=h),analyse_ticker=MagicMock(return_value=None)), \
                    patch.object(b,'fetch_news_sentiment') as news,contextlib.redirect_stdout(io.StringIO()):
                    result=b.run_backtest();news.assert_not_called()
                    self.assertEqual(b.download_history.call_args.kwargs['end'],'2026-11-14')
                    self.assertEqual(len(result),1)
                self.assertTrue(pd.read_csv('backtest_results/marktsent_trades.csv').empty)
                self.assertEqual(len(pd.read_csv('backtest_results/marktsent_summary_per_ticker.csv')),1)
            finally:os.chdir(cwd)

    def test_workflow_services(self):
        import yaml
        doc=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows/run_01marktsent.yml').read_text())
        step=doc['jobs']['bot-01marktsent']['steps'][-1]
        self.assertIn('NEWSAPI_KEY',step['env']);self.assertIn('SUPABASE_DB_URL',step['env'])
        self.assertIn('EMAIL_PASS',step['env']);self.assertNotIn('TELEGRAM_TOKEN',step['env'])
        self.assertEqual(step['env']['PYTHONUNBUFFERED'],'1')

if __name__=='__main__':unittest.main()
