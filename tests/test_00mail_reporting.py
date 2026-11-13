"""Offline controle: uitsluitend gesimuleerde downloads en verzending."""
import contextlib
from datetime import datetime
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from test_00kr_data_fix import history
import bot_00mail as bot


def recent_history(ticker="OK", count=300):
    frame = history(count)
    frame["Date"] = pd.bdate_range(end=pd.Timestamp(bot.today_str()), periods=count)
    frame["Ticker"] = ticker
    return frame


class MailReportingTests(unittest.TestCase):
    def sector(self, raw, tickers=("OK",), **kwargs):
        with patch.object(bot.os.path, "exists", return_value=True), \
             patch.object(bot, "load_tickers_from_file", return_value=list(tickers)), \
             patch.object(bot, "download_history", return_value=raw):
            return bot.voer_lijst_uit("list.txt", "01", "Test", **kwargs)

    def test_failed_downloads_are_in_report_and_partial_label(self):
        report = self.sector(recent_history(), ("OK", "MISSING"))
        self.assertIn("2 gevraagd | 1 analyseerbaar | 1 overgeslagen", report)
        self.assertIn("MISSING: geen koersdata", report)
        self.assertIn("STATUS: ONVOLLEDIG", report)

    def test_no_data_never_reports_zero_profit_as_real_result(self):
        status = {"analyses": 0, "fouten": 0, "overgeslagen": 0}
        report = self.sector(pd.DataFrame(), status=status)
        self.assertIn("P/L:* niet beschikbaar", report)
        self.assertIn("nul geslaagde analyses", report)
        self.assertGreater(status["fouten"], 0)

    def test_invalid_and_duplicate_rows_are_removed_without_filling(self):
        frame = recent_history(count=4)
        frame.loc[1, "Close"] = np.nan
        frame.loc[2, "High"] = np.inf
        data = bot.prepare_history(pd.concat([frame.iloc[::-1], frame.iloc[[0]]]))
        self.assertEqual(len(data), 2)
        self.assertTrue(data.index.is_monotonic_increasing)
        self.assertFalse(data.isna().any().any())

    def test_stale_history_is_visible_and_not_analysed(self):
        frame = recent_history()
        frame["Date"] -= pd.Timedelta(days=30)
        report = self.sector(frame)
        self.assertIn("verouderde koersdata", report)
        self.assertIn("0 analyseerbaar", report)

    def test_analysis_exceptions_are_not_suppressed(self):
        status = {"analyses": 0, "fouten": 0, "overgeslagen": 0}
        with patch.object(bot, "bereken_indicatoren_vectorized", side_effect=ValueError("broken")), \
             self.assertLogs(bot.logger, level="ERROR") as logs:
            report = self.sector(recent_history(), status=status)
        self.assertEqual(status["fouten"], 4)
        self.assertEqual(status["analyses"], 0)
        self.assertIn("analysefout (ValueError: broken)", report)
        self.assertTrue(any("Analysefout" in line for line in logs.output))

    def test_negative_download_cache_avoids_retries_between_sectors(self):
        cache = {}
        with patch.object(bot, "download_history", return_value=recent_history()) as download:
            bot.cached_history(["OK", "MISSING"], cache)
            bot.cached_history(["OK", "MISSING"], cache)
        self.assertEqual(download.call_count, 1)
        self.assertTrue(cache["MISSING"].empty)

    def test_missing_list_raises_visible_sector_error(self):
        with patch.object(bot.os.path, "exists", return_value=False):
            with self.assertRaises(FileNotFoundError):
                bot.voer_lijst_uit("missing", "01", "Test")

    def run_main(self, delivered=True, sector_error=False, analysed=True):
        def fake_sector(*args, **kwargs):
            if sector_error:
                raise ValueError("sector broken")
            kwargs["status"]["analyses"] += int(analysed)
            return "Sectorrapport\n"
        with patch.object(bot, "voer_lijst_uit", side_effect=fake_sector), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "send_email", return_value=delivered) as sender:
            result = bot.main()
        return result, sender.call_args

    def test_failed_email_sets_failure_exit_code(self):
        with self.assertLogs(bot.logger, level="ERROR") as logs:
            result, _ = self.run_main(delivered=False)
        self.assertEqual(result, 1)
        self.assertTrue(any("E-mail NIET verstuurd" in text for text in logs.output))

    def test_sector_exception_is_in_email_and_fails_job(self):
        with self.assertLogs(bot.logger, level="ERROR"):
            result, args = self.run_main(sector_error=True)
        self.assertEqual(result, 1)
        self.assertIn("[ONVOLLEDIG]", args.args[0])
        self.assertIn("SECTOR Hoogland: MISLUKT", args.args[1])

    def test_zero_analyses_fails_even_if_email_was_sent(self):
        with self.assertLogs(bot.logger, level="ERROR"):
            result, args = self.run_main(analysed=False)
        self.assertEqual(result, 1)
        self.assertIn("[ONVOLLEDIG]", args.args[0])

    def test_successful_email_reports_success_and_mail_has_no_telegram_sender(self):
        with self.assertLogs(bot.logger, level="INFO") as logs:
            result, args = self.run_main()
        self.assertEqual(result, 0)
        self.assertNotIn("[ONVOLLEDIG]", args.args[0])
        self.assertTrue(any("E-mail succesvol verstuurd" in text for text in logs.output))
        self.assertFalse(hasattr(bot, "send_telegram"))


if __name__ == "__main__":
    unittest.main()
