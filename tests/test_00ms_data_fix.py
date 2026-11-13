"""Offline regressietests voor de Minervini-livegegevensstroom."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from test_00kr_data_fix import history
import bot_00ms as bot


class MinerviniTests(unittest.TestCase):
    def universe(self):
        frames = []
        for ticker, growth in (("EU.PA", 0.2), ("US", 0.4)):
            frame = history(400)
            price = 100 + np.arange(len(frame)) * growth
            for col, offset in (("Open", 0), ("Close", 0), ("High", 2), ("Low", -2)):
                frame[col] = price + offset
            frame["Ticker"] = ticker
            frames.append(frame)
        return pd.concat(frames, ignore_index=True)

    def polluted(self, frame):
        extra = frame.loc[frame["Ticker"] == "EU.PA"].iloc[[-1]].copy()
        extra["Date"] += pd.Timedelta(days=1)
        extra[["Open", "High", "Low", "Close"]] = np.nan
        extra["Volume"] = 0
        return pd.concat([frame, extra], ignore_index=True)

    def test_rs_includes_europe_after_empty_final_row(self):
        clean = self.universe()
        expected = bot.compute_rs_ratings(clean)
        actual = bot.compute_rs_ratings(self.polluted(clean))
        self.assertEqual(set(actual), {"EU.PA", "US"})
        self.assertEqual(actual, expected)

    def test_indicators_ignore_empty_calendar_row_and_sort_before_rolling(self):
        clean = self.universe()
        ratings = bot.compute_rs_ratings(clean)
        expected = bot.add_indicators(clean, ratings)
        actual = bot.add_indicators(self.polluted(clean).iloc[::-1], ratings)
        pd.testing.assert_frame_equal(actual, expected)

    def test_rs_rejects_short_history(self):
        frame = history(100)
        frame["Ticker"] = "SHORT"
        self.assertEqual(bot.compute_rs_ratings(frame), {})

    def test_missing_rs_is_explicitly_unavailable(self):
        result = bot.add_indicators(self.universe(), {})
        self.assertTrue(result["RS"].isna().all())

    def run_offline(self, candidate=False):
        frame = self.universe()
        signal = bot.SEPASignaal(
            ticker="EU.PA", price=179.8, trend_passed=True, trend_details=[],
            vcp_score=2, vcp_details=[], pivot=180, stop=170, rs=80,
            pct_from_high=0, pct_from_low=30, atr=4, total_score=70)
        output = io.StringIO()
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.object(bot, "bouw_bestandslijst", return_value=["tickers_042x.txt"]), \
                     patch.object(bot, "load_tickers_from_file", return_value=["EU.PA", "MISSING"]), \
                     patch.object(bot, "download_history", return_value=frame), \
                     patch.object(bot, "analyse_ticker", return_value=signal if candidate else None), \
                     patch.object(bot, "log_selectie"), \
                     patch.object(bot, "send_telegram", return_value=False), \
                     patch.object(bot, "send_email", return_value=True), \
                     contextlib.redirect_stdout(output):
                    bot.run_live_engine()
                return output.getvalue(), pd.read_csv(f"ms_signalen_{bot.today_str()}.csv")
            finally:
                os.chdir(old_cwd)

    def test_live_writes_candidates_and_truthful_telegram_status(self):
        output, csv = self.run_offline(candidate=True)
        self.assertEqual(csv["ticker"].tolist(), ["EU.PA"])
        self.assertIn("Telegram niet verstuurd", output)
        self.assertIn("1 beoordeeld", output)
        self.assertIn("1 zonder koersdata", output)

    def test_live_distinguishes_rejection_from_missing_data(self):
        output, csv = self.run_offline()
        self.assertTrue(csv.empty)
        self.assertIn("1 voldoen niet aan SEPA", output)
        self.assertIn("ticker", csv.columns)


if __name__ == "__main__":
    unittest.main()
