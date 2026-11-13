"""Offline regression tests; never sends notifications or writes a database."""
import sys
import math
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import replace
from types import SimpleNamespace
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for module in ('yfinance', 'requests', 'dotenv'):
    try:
        __import__(module)
    except ImportError:
        sys.modules[module] = MagicMock()
import bot_00oshaughnessy as bot

DATE = pd.Timestamp('2025-12-31')

def fixture():
    def frame(values):
        return pd.DataFrame({DATE: values})
    return SimpleNamespace(
        info=dict(currentPrice=10, currency='USD', financialCurrency='USD', marketCap=1e9,
                  trailingPE=10, priceToBook=2, priceToSalesTrailing12Months=.5,
                  enterpriseValue=8e8, enterpriseToEbitda=8),
        financials=frame({'Total Revenue': 2e9, 'EBITDA': 1e8}),
        balance_sheet=frame({'Total Assets': 2e9}),
        cashflow=frame({'Operating Cash Flow': 2e8, 'Capital Expenditure': -5e7,
                        'Common Stock Dividend Paid': -2e7, 'Net Common Stock Issuance': -3e7}))

def raw(ticker='A', value=10):
    return bot.RuweSignaal(ticker, 'X', 10, value, value, value, value, value, value, 5, 'USD', 'USD', '2025-12-31')

class Fundamentals(unittest.TestCase):
    def setUp(self):
        self.date = patch.object(bot, 'today_str', return_value='2026-11-13'); self.date.start()
        self.addCleanup(self.date.stop)
        self.tk = fixture(); self.diag = {}
    def analyse(self):
        with patch.object(bot.yf, 'Ticker', return_value=self.tk):
            return bot.analyse_ticker_ruw('A', 'X', bot.OSHAUGHNESSY_CFG, self.diag)
    def test_cash_ratios_and_yield(self):
        s = self.analyse()
        self.assertEqual(s.pcf, 5); self.assertAlmostEqual(s.ev_fcf, 8e8/1.5e8)
        self.assertEqual(s.shareholder_yield, 5); self.assertEqual(s.fiscal_date, '2025-12-31')
    def test_provider_dividend_unit_does_not_change_yield(self):
        self.tk.info['dividendYield'] = 99
        self.assertEqual(self.analyse().shareholder_yield, 5)
    def test_missing_shareholder_data_is_not_zero(self):
        self.tk.cashflow = self.tk.cashflow.drop('Net Common Stock Issuance')
        self.assertTrue(math.isnan(self.analyse().shareholder_yield))
    def test_explicit_zero_yield(self):
        self.tk.cashflow.loc[['Net Common Stock Issuance', 'Common Stock Dividend Paid']] = 0
        self.assertEqual(self.analyse().shareholder_yield, 0)
    def test_net_issuance_can_make_negative_yield(self):
        self.tk.cashflow.loc['Net Common Stock Issuance'] = 5e7
        self.assertEqual(self.analyse().shareholder_yield, -3)
    def test_cash_issuance_fallback(self):
        self.tk.cashflow = self.tk.cashflow.drop('Net Common Stock Issuance')
        self.tk.cashflow.loc['Repurchase Of Capital Stock'] = -4e7
        self.tk.cashflow.loc['Issuance Of Capital Stock'] = 1e7
        self.assertEqual(self.analyse().shareholder_yield, 5)
    def test_missing_debt_not_assumed_zero(self):
        self.tk.info.pop('enterpriseValue')
        self.assertTrue(math.isnan(self.analyse().ev_fcf))
    def test_explicit_zero_debt_and_cash_valid(self):
        self.tk.info.update(enterpriseValue=None, totalDebt=0, totalCash=0)
        self.assertAlmostEqual(self.analyse().ev_fcf, 1e9/1.5e8)
    def test_different_fiscal_years_rejected(self):
        self.tk.cashflow.columns = [pd.Timestamp('2024-12-31')]
        self.assertIsNone(self.analyse()); self.assertEqual(self.diag['A'][0], 'data')
    def test_stale_fiscal_year_rejected(self):
        for f in (self.tk.cashflow, self.tk.balance_sheet, self.tk.financials):
            f.columns = [pd.Timestamp('2023-12-31')]
        self.assertIsNone(self.analyse())
    def test_currency_mismatch_rejected(self):
        self.tk.info['financialCurrency'] = 'EUR'
        self.assertIsNone(self.analyse())
    def test_usd_floor_converts_currency(self):
        self.tk.info.update(currency='EUR', financialCurrency='EUR', marketCap=1.9e8)
        with patch.object(bot, '_usd_rate', return_value=1.1):
            self.assertIsNotNone(self.analyse())
        with patch.object(bot, '_usd_rate', return_value=1):
            self.assertIsNone(self.analyse()); self.assertEqual(self.diag['A'][0], 'filter')
    def test_invalid_price_rejected(self):
        self.tk.info['currentPrice'] = float('inf')
        self.assertIsNone(self.analyse())

class Composite(unittest.TestCase):
    def test_ineligible_record_does_not_distort_ranking(self):
        bad = replace(raw('BAD', .01), pb=math.nan, ps=math.nan, pcf=math.nan, ev_fcf=math.nan)
        df = bot.bereken_vc2([raw(), raw('B', 20), bad], bot.OSHAUGHNESSY_CFG).set_index('ticker')
        self.assertEqual(len(df), 2); self.assertEqual(df.loc['A', 'pct_pe'], 100)
        self.assertEqual(df.loc['B', 'pct_pe'], 50)
    def test_negative_yield_counts_as_available(self):
        df = bot.bereken_vc2([replace(raw(), shareholder_yield=-2)], bot.OSHAUGHNESSY_CFG)
        self.assertEqual(df.iloc[0].vc2_geldige_ratios, 7)
    def test_equal_factors_equal_ranks(self):
        df = bot.bereken_vc2([raw(), raw('B')], bot.OSHAUGHNESSY_CFG)
        self.assertEqual(df.iloc[0].vc2_score, df.iloc[1].vc2_score)
    def test_empty_universe_has_score_column(self):
        df = bot.bereken_vc2([], bot.OSHAUGHNESSY_CFG)
        self.assertTrue(df.empty); self.assertIn('vc2_score', df)
    def test_duplicate_ticker_not_double_weighted(self):
        self.assertEqual(len(bot.bereken_vc2([raw(), raw()], bot.OSHAUGHNESSY_CFG)), 1)

class Momentum(unittest.TestCase):
    def momentum(self, dates, prices):
        tk = MagicMock(); tk.history.return_value = pd.DataFrame({'Close': prices}, index=pd.to_datetime(dates))
        with patch.object(bot, 'today_str', return_value='2026-11-13'), patch.object(bot.yf, 'Ticker', return_value=tk):
            result = bot.bereken_momentum_pct('A', 6)
        self.assertTrue(tk.history.call_args.kwargs['auto_adjust'])
        return result
    def test_full_calendar_span_and_precision(self):
        self.assertAlmostEqual(self.momentum(['2026-05-13','2026-11-13'], [10, 12.34567]), 23.4567)
    def test_short_ipo_history_rejected(self):
        self.assertTrue(math.isnan(self.momentum(['2026-10-13','2026-11-13'], [10, 12])))
    def test_stale_last_quote_rejected(self):
        self.assertTrue(math.isnan(self.momentum(['2026-05-13','2026-11-01'], [10, 12])))
    def test_future_and_nan_quotes_ignored(self):
        self.assertAlmostEqual(self.momentum(['2026-11-15','2026-11-13','2026-05-13','2026-11-14'], [100,12,10,math.nan]), 20)
    def test_timezone_preserved_as_session_date(self):
        self.assertAlmostEqual(self.momentum(['2026-05-13T00:00:00-04:00','2026-11-13T00:00:00-04:00'], [10,12]),20)

class Live(unittest.TestCase):
    def run_bot(self, category='ok', db=True, email=True):
        def analyse(ticker, exchange, cfg, diagnostics, fx_cache):
            diagnostics[ticker] = (category, 'test')
            return raw(ticker) if category == 'ok' else None
        with patch.multiple(bot, bouw_bestandslijst=MagicMock(return_value=['one','two']),
                            load_tickers_from_file=MagicMock(return_value=['A']), label_voor=lambda x:x,
                            analyse_ticker_ruw=MagicMock(side_effect=analyse),
                            bereken_momentum_pct=MagicMock(return_value=12),
                            log_selectie=MagicMock(return_value=db), send_email=MagicMock(return_value=email),
                            send_telegram=MagicMock(), today_str=MagicMock(return_value='2026-11-13')) as mocks, \
             patch.object(bot.time, 'sleep'), patch.dict(bot.os.environ, {}, clear=True):
            error = None
            try: bot.run_live_engine()
            except RuntimeError as exc: error = exc
            return {name: getattr(bot, name) for name in ('analyse_ticker_ruw', 'send_email', 'send_telegram')}, error
    def test_overlap_downloaded_once_telegram_off(self):
        mocks,error = self.run_bot()
        self.assertIsNone(error); self.assertEqual(mocks['analyse_ticker_ruw'].call_count,1)
        mocks['send_telegram'].assert_not_called()
        self.assertIn('1 uniek gescreend', mocks['send_email'].call_args.args[1])
    def test_email_failure_fails_run(self):
        _,error = self.run_bot(email=False); self.assertIsNotNone(error)
    def test_database_failure_reported_and_fails_run(self):
        mocks,error = self.run_bot(db=False)
        self.assertIsNotNone(error); self.assertIn('[ONVOLLEDIG]', mocks['send_email'].call_args.args[0])
    def test_all_missing_data_report_then_fail(self):
        mocks,error = self.run_bot(category='data')
        self.assertIsNotNone(error); mocks['send_email'].assert_called_once()
    def test_pure_filter_empty_selection_is_valid(self):
        _,error = self.run_bot(category='filter'); self.assertIsNone(error)

if __name__ == '__main__': unittest.main()
