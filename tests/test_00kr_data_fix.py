"""Offline regressietests; geen Yahoo-, database- of notificatieverkeer."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

# Offline tests kunnen ook zonder de netwerkclients worden uitgevoerd.
for name in ("requests", "yfinance"):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = MagicMock()

import common.io as data_io
import common.notify as notify
import bot_00kr as bot


def history(n=800):
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = 100 + np.sin(np.arange(n) / 20) * 10 + np.arange(n) / 100
    return pd.DataFrame({"Date": dates, "Open": close, "High": close + 2,
                         "Low": close - 2, "Close": close, "Volume": 1000})


class NormalisationTests(unittest.TestCase):
    def test_empty_close_with_volume_zero_is_removed(self):
        raw = history(2).set_index("Date")
        raw.loc[raw.index[-1], ["Open", "High", "Low", "Close"]] = np.nan
        raw.loc[raw.index[-1], "Volume"] = 0
        result = data_io._normalise(raw, "EU.PA")
        self.assertEqual(len(result), 1)
        self.assertEqual(result["Close"].iloc[-1], raw["Close"].iloc[0])

    def test_invalid_prices_and_dates_are_removed(self):
        raw = history(6)
        raw["Close"] = ["100", "invalid", 0, -1, np.inf, 200]
        raw.loc[5, "Date"] = pd.NaT
        result = data_io._normalise(raw, "EU.PA")
        self.assertEqual(result["Close"].tolist(), [100])

    def test_both_multiindex_layouts(self):
        raw = history(2).set_index("Date")
        for ticker_first in (False, True):
            with self.subTest(ticker_first=ticker_first):
                multi = pd.concat({"EU.PA": raw}, axis=1)
                if not ticker_first:
                    multi = multi.swaplevel(axis=1)
                result = data_io._normalise(multi, "EU.PA")
                self.assertEqual(result["Close"].tolist(), raw["Close"].tolist())

    def test_timezone_keeps_local_trading_date(self):
        raw = history(2).set_index("Date")
        raw.index = raw.index.tz_localize("Europe/Brussels")
        result = data_io._normalise(raw, "EU.PA")
        self.assertEqual(result["Date"].iloc[0], pd.Timestamp("2023-01-02"))
        self.assertIsNone(result["Date"].dt.tz)

    def test_partial_batch_retries_only_missing_ticker(self):
        raw = history(2).set_index("Date")
        empty = raw.astype(float).copy()
        empty.loc[:, :] = np.nan
        batch = pd.concat({"OK": raw, "MISSING": empty}, axis=1)
        with patch.object(data_io.yf, "download", side_effect=[batch, raw]) as download, \
                patch.object(data_io.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            result = data_io.download_history(["OK", "MISSING"])
        self.assertEqual(set(result["Ticker"]), {"OK", "MISSING"})
        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args.kwargs["tickers"], "MISSING")

    def test_no_usable_data_returns_empty(self):
        with patch.object(data_io.yf, "download", return_value=pd.DataFrame()), \
                contextlib.redirect_stdout(io.StringIO()):
            result = data_io.download_history(["MISSING"])
        self.assertTrue(result.empty)


class AnalysisTests(unittest.TestCase):
    def test_trailing_batch_calendar_rows_do_not_change_signal(self):
        clean = history()
        empty = pd.DataFrame({"Date": [clean["Date"].iloc[-1] + pd.Timedelta(days=1)],
                              "Close": [np.nan], "High": [np.nan],
                              "Low": [np.nan], "Volume": [0]})
        dirty = pd.concat([clean, empty], ignore_index=True)
        expected = bot.analyse_ticker("EU.PA", clean, include_current_dividend=False)
        actual = bot.analyse_ticker("EU.PA", dirty, include_current_dividend=False)
        self.assertIsNotNone(expected)
        self.assertEqual(actual, expected)

    def test_insufficient_valid_rows_explains_skip(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            signal = bot.analyse_ticker("SHORT", history(20), False, report_skips=True)
        self.assertIsNone(signal)
        self.assertIn("20 geldige koersrijen", output.getvalue())


class LiveTests(unittest.TestCase):
    def run_offline(self, *, candidate, telegram=False):
        frame = history()
        frame["Ticker"] = "EU.PA"
        signal = bot.analyse_ticker("EU.PA", frame, include_current_dividend=False)
        signal.score = 3 if candidate else 0
        output = io.StringIO()
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.object(bot, "bouw_bestandslijst", return_value=["tickers_042x.txt"]), \
                     patch.object(bot, "load_tickers_from_file", return_value=["EU.PA", "MISSING"]), \
                     patch.object(bot, "download_history", return_value=frame), \
                     patch.object(bot, "analyse_ticker", return_value=signal), \
                     patch.object(bot, "log_selectie"), \
                     patch.object(bot, "send_telegram", return_value=telegram) as send, \
                     patch.object(bot, "send_email", return_value=True), \
                     contextlib.redirect_stdout(output):
                    bot.run_live_engine()
                csv = pd.read_csv(f"ks_signalen_{bot.today_str()}.csv")
                return output.getvalue(), csv, send.call_count
            finally:
                os.chdir(old_cwd)

    def test_csv_signal_and_failed_telegram(self):
        output, csv, calls = self.run_offline(candidate=True)
        self.assertEqual(csv["ticker"].tolist(), ["EU.PA"])
        self.assertEqual(calls, 1)
        self.assertIn("Telegram niet verstuurd", output)
        self.assertNotIn("→ Telegram verstuurd", output)
        self.assertIn("Geen koersdata (1): MISSING", output)

    def test_successful_telegram(self):
        output, _, _ = self.run_offline(candidate=True, telegram=True)
        self.assertIn("→ Telegram verstuurd", output)

    def test_no_candidates_still_writes_csv_headers(self):
        _, csv, calls = self.run_offline(candidate=False)
        self.assertTrue(csv.empty)
        self.assertIn("ticker", csv.columns)
        self.assertEqual(calls, 0)

    def test_no_download_data_fails_job(self):
        with patch.object(bot, "bouw_bestandslijst", return_value=["x"]), \
             patch.object(bot, "load_tickers_from_file", return_value=["EU.PA"]), \
             patch.object(bot, "download_history", return_value=pd.DataFrame()), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Geen bruikbare koersdata"):
                bot.run_live_engine()


class NotificationTests(unittest.TestCase):
    def response(self, status, ok):
        return MagicMock(status_code=status, json=MagicMock(return_value={"ok": ok}))

    def test_plain_text_fallback_success_is_reported(self):
        with patch.object(notify, "_tg_creds", return_value=("dummy", "dummy")), \
             patch.object(notify.requests, "post", side_effect=[
                 self.response(400, False), self.response(200, True)]) as post:
            self.assertTrue(notify.send_telegram("test"))
        self.assertNotIn("parse_mode", post.call_args.kwargs["json"])

    def test_api_failure_is_not_success(self):
        with patch.object(notify, "_tg_creds", return_value=("dummy", "dummy")), \
             patch.object(notify.requests, "post", return_value=self.response(200, False)):
            self.assertFalse(notify.send_telegram("test"))

    def test_missing_secrets_do_not_send(self):
        with patch.object(notify, "_tg_creds", return_value=("", "")), \
             patch.object(notify.requests, "post") as post:
            self.assertFalse(notify.send_telegram("test"))
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
