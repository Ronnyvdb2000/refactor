"""Offline regressietests voor data, ranking en rapportage van Greenblatt."""
import contextlib
from copy import deepcopy
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

for name in ("requests", "yfinance"):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = MagicMock()

import bot_00greenblatt as bot


def company(**info_changes):
    info = dict(currentPrice=10, sector="Industrials", marketCap=100_000_000,
                currency="USD", financialCurrency="USD", enterpriseValue=80_000_000)
    info.update(info_changes)
    date = pd.Timestamp("2025-12-31")
    income = pd.DataFrame({date: {"EBIT": 8_000_000}})
    balance = pd.DataFrame({date: {"Current Assets": 40_000_000,
                                  "Current Liabilities": 10_000_000,
                                  "Net PPE": 50_000_000}})
    return MagicMock(info=info, financials=income, balance_sheet=balance)


class DataTests(unittest.TestCase):
    def analyse(self, obj=None, **kwargs):
        self.diagnostics = {}
        with patch.object(bot.yf, "Ticker", return_value=obj if obj is not None else company()), \
             patch.object(bot, "today_str", return_value="2026-11-13"), \
             contextlib.redirect_stdout(io.StringIO()):
            return bot.analyse_ticker("TEST", "Test", bot.GREENBLATT_CFG,
                                      diagnostics=self.diagnostics, **kwargs)

    def test_known_ratios_and_source_metadata(self):
        sig = self.analyse()
        self.assertAlmostEqual(sig.roc, 0.1)
        self.assertAlmostEqual(sig.earnings_yield, 0.1)
        self.assertEqual(sig.invested_capital, 80_000_000)
        self.assertEqual(sig.fiscal_date, "2025-12-31")
        self.assertEqual(sig.price_currency, "USD")

    def test_nonfinite_market_cap_is_rejected(self):
        self.assertIsNone(self.analyse(company(marketCap=float("inf"))))

    def test_nonfinite_price_falls_back_to_valid_quote(self):
        sig = self.analyse(company(currentPrice=float("inf"), regularMarketPrice=20))
        self.assertEqual(sig.price, 20)

    def test_nonfinite_price_without_fallback_is_rejected(self):
        self.assertIsNone(self.analyse(company(currentPrice=float("inf"))))

    def test_nontangible_asset_total_cannot_replace_ppe(self):
        obj = company()
        obj.balance_sheet = obj.balance_sheet.rename(index={"Net PPE": "Net Tangible Assets"})
        self.assertIsNone(self.analyse(obj))
        self.assertIn("Net PPE", self.diagnostics["TEST"][1])

    def test_missing_ev_does_not_assume_debt_and_cash_zero(self):
        self.assertIsNone(self.analyse(company(enterpriseValue=None)))
        self.assertIn("schuld/cash", self.diagnostics["TEST"][1])

    def test_ev_fallback_with_complete_values(self):
        sig = self.analyse(company(enterpriseValue=None, totalDebt=20_000_000, totalCash=10_000_000))
        self.assertEqual(sig.enterprise_value, 110_000_000)

    def test_mixed_currencies_are_explicitly_excluded(self):
        self.assertIsNone(self.analyse(company(currency="EUR", financialCurrency="USD")))
        self.assertIn("valutaverschil", self.diagnostics["TEST"][1])

    def test_missing_currency_is_not_assumed_eur(self):
        self.assertIsNone(self.analyse(company(currency=None)))

    def test_small_market_cap_filter_is_in_usd(self):
        obj = company(currency="EUR", financialCurrency="EUR", marketCap=45_000_000)
        sig = self.analyse(obj, fx_cache={"EUR": 1.2})
        self.assertIsNotNone(sig)
        self.assertEqual(sig.market_cap_usd, 54_000_000)

    def test_quote_in_pence_preserves_label_and_does_not_scale_market_cap(self):
        sig = self.analyse(company(currency="GBp", financialCurrency="GBP"), fx_cache={"GBP": 1.3})
        self.assertEqual(sig.price_currency, "GBp")
        self.assertEqual(sig.market_cap_usd, 130_000_000)

    def test_stale_financials_are_rejected(self):
        obj = company()
        obj.financials.columns = obj.balance_sheet.columns = [pd.Timestamp("2022-12-31")]
        self.assertIsNone(self.analyse(obj))

    def test_unequal_fiscal_dates_are_rejected(self):
        obj = company()
        obj.balance_sheet.columns = [pd.Timestamp("2024-12-31")]
        self.assertIsNone(self.analyse(obj))

    def test_ebit_falls_back_to_operating_income_for_same_year(self):
        obj = company()
        obj.financials.loc["Operating Income"] = 7_000_000
        obj.financials.loc["EBIT"] = float("nan")
        self.assertEqual(self.analyse(obj).ebit, 7_000_000)

    def test_filters_and_data_errors_are_separated(self):
        self.assertIsNone(self.analyse(company(sector="Financial Services")))
        self.assertEqual(self.diagnostics["TEST"][0], "filter")
        self.assertIsNone(self.analyse(company(sector=None)))
        self.assertEqual(self.diagnostics["TEST"][0], "data")

    def test_recent_fx_download_is_cached(self):
        client = MagicMock()
        client.history.return_value = pd.DataFrame({"Close": [1.2]}, index=pd.to_datetime(["2026-11-12"]))
        cache = {}
        with patch.object(bot.yf, "Ticker", return_value=client) as ticker, \
             patch.object(bot, "today_str", return_value="2026-11-13"):
            self.assertEqual(bot._usd_rate("EUR", cache), 1.2)
            self.assertEqual(bot._usd_rate("EUR", cache), 1.2)
        ticker.assert_called_once_with("EURUSD=X")

    def test_stale_fx_is_not_used(self):
        client = MagicMock()
        client.history.return_value = pd.DataFrame({"Close": [1.2]}, index=pd.to_datetime(["2020-01-01"]))
        with patch.object(bot.yf, "Ticker", return_value=client):
            with self.assertRaises(bot._SkipTicker):
                bot._usd_rate("EUR", {})


class RankingTests(unittest.TestCase):
    def signals(self):
        return [bot.GreenblattSignaal(t, "Test", 10, "Industrials", 1, 10, roc, 10, ey)
                for t, roc, ey in (("A", .2, .1), ("B", .2, .3), ("C", .1, .1))]

    def test_ties_have_equal_ranks(self):
        items = self.signals()
        bot.rangschik_globaal(items)
        self.assertEqual([s.roc_rank for s in items], [1, 1, 3])
        self.assertEqual([s.ey_rank for s in items], [2, 1, 2])

    def test_ranking_is_independent_of_input_order(self):
        a = bot.rangschik_globaal(self.signals())
        b = bot.rangschik_globaal(list(reversed(self.signals())))
        self.assertEqual([(s.ticker, s.combined_rank) for s in a], [(s.ticker, s.combined_rank) for s in b])


class LiveTests(unittest.TestCase):
    def run_scan(self, email=True, db=True, obj=None):
        output = io.StringIO()
        with patch.object(bot, "bouw_bestandslijst", return_value=["A", "B"]), \
             patch.object(bot, "label_voor", side_effect=lambda name: name), \
             patch.object(bot, "load_tickers_from_file", return_value=["TEST"]), \
             patch.object(bot.yf, "Ticker", return_value=obj if obj is not None else company()) as ticker, \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "log_selectie", return_value=db) as logger, \
             patch.object(bot, "send_email", return_value=email) as sender, \
             patch.object(bot, "send_telegram") as telegram, \
             patch.dict(os.environ, {"TELEGRAM_TOKEN": "", "TELEGRAM_CHAT_ID": ""}), \
             patch.object(bot, "today_str", return_value="2026-11-13"), \
             contextlib.redirect_stdout(output):
            try:
                bot.run_live_engine()
                error = None
            except RuntimeError as exc:
                error = str(exc)
        return output.getvalue(), ticker, logger, sender, telegram, error

    def test_overlapping_exchange_is_counted_but_fetched_and_saved_once(self):
        output, ticker, logger, sender, telegram, error = self.run_scan()
        self.assertIsNone(error)
        self.assertEqual(output.count("→ 1/1 tickers"), 2)
        ticker.assert_called_once_with("TEST")
        self.assertEqual(logger.call_count, 1)
        telegram.assert_not_called()
        self.assertIn("USD 10.00", sender.call_args.args[1])
        self.assertNotIn("EUR10.00", sender.call_args.args[1])

    def test_mail_failure_is_not_success(self):
        *_, error = self.run_scan(email=False)
        self.assertIn("E-mail niet verstuurd", error)

    def test_database_failure_is_reported_and_fails(self):
        output, _, _, sender, _, error = self.run_scan(db=False)
        self.assertIn("Database: 0/1", output)
        self.assertIn("[ONVOLLEDIG]", sender.call_args.args[0])
        self.assertIsNotNone(error)

    def test_zero_candidates_due_to_data_fails_with_report(self):
        _, _, _, sender, _, error = self.run_scan(obj=company(currency=None))
        self.assertIsNotNone(error)
        self.assertIn("Geen gekwalificeerde kandidaten", sender.call_args.args[1])

    def test_zero_candidates_due_to_criteria_is_valid_empty_result(self):
        _, _, _, sender, _, error = self.run_scan(obj=company(sector="Utilities"))
        self.assertIsNone(error)
        self.assertNotIn("[ONVOLLEDIG]", sender.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
