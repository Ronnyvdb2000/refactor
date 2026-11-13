"""Offline tests voor ULT-uitvoering, datadekking en verzending."""
import contextlib
import io
import unittest
from unittest.mock import patch

import pandas as pd
from test_00kr_data_fix import history
from test_00mail_reporting import recent_history
import bot_00ultmail as bot


class ExecutionTests(unittest.TestCase):
    def series(self, values):
        return pd.Series(values, dtype=float)

    def trend(self, closes, opens):
        n = len(closes)
        s = self.series
        data = pd.DataFrame({"Open": opens})
        fast = s([0] + [2] * (n-1))
        indicators = (s(closes), fast, s([1]*n), s([0]*n), s([50]*n),
                      s([50]*n), s([1]*n), s([20]*n), s([100]*n))
        return data, indicators

    def test_trend_uses_next_open_and_exit_notional_fee(self):
        data, ind = self.trend([10,11,10,10], [10,11,20,40])
        pnl, entries = bot.simuleer_trend(data, ind, True, inzet=10000)
        # Signaal op index 1 -> koop op index 2 voor 20, verkoop op index 3
        # voor 40. Kosten 35 bij instap, 70 over werkelijke uitstapnotional.
        self.assertEqual(entries, 1)
        self.assertAlmostEqual(pnl, 9895)

    def test_unfinished_trend_marks_close_without_exit_fee(self):
        data, ind = self.trend([10,11,25,30], [10,11,20,40])
        pnl, entries = bot.simuleer_trend(data, ind, True, inzet=10000)
        self.assertEqual(entries, 1)
        self.assertAlmostEqual(pnl, 4965)

    def test_last_close_signal_cannot_fill_on_same_close(self):
        data, ind = self.trend([10,11,12], [10,11,20])
        ind = list(ind)
        ind[1] = self.series([0,0,2])
        pnl, entries = bot.simuleer_trend(data, ind, True)
        self.assertEqual((pnl, entries), (0,0))

    def test_old_trend_entry_outside_252_session_window_is_not_counted(self):
        data, ind = self.trend([10]*600, [10]*600)
        self.assertEqual(bot.simuleer_trend(data, ind, True), (0,0))

    def test_mra_uses_next_open_for_entry_and_exit(self):
        s=self.series
        data=pd.DataFrame({"Open":[10,10,20,40]})
        pnl, entries=bot.simuleer_mra(data,s([10,8,9,12]),s([0,10,0,0]),
                                     s([1,.1,1,1]),s([99,99,8,8]),1.12,inzet=10000)
        self.assertEqual(entries,1)
        self.assertAlmostEqual(pnl,9895)

    def test_mra_minimum_hold_uses_execution_sessions(self):
        s=self.series
        n=9
        data=pd.DataFrame({"Open":[10,10,20,30,40,50,60,70,80]})
        pnl,entries=bot.simuleer_mra(data,s([10,8]+[11]*(n-2)),s([0,10]+[0]*(n-2)),
                                    s([1,.1]+[1]*(n-2)),s([0]*n),1.25,min_hold=5,inzet=10000)
        self.assertEqual(entries,1)
        self.assertAlmostEqual(pnl,25000-35-122.5)

    def test_mra_open_position_has_no_fictitious_exit_cost(self):
        s=self.series
        data=pd.DataFrame({"Open":[10,10,20,40]})
        pnl,entries=bot.simuleer_mra(data,s([10,8,25,30]),s([0,10,0,0]),
                                    s([1,.1,1,1]),s([99]*4),100,inzet=10000)
        self.assertEqual(entries,1)
        self.assertAlmostEqual(pnl,4965)


class ReportingTests(unittest.TestCase):
    def sector(self, raw, tickers=("OK",), status=None):
        with patch.object(bot.os.path,"exists",return_value=True), \
             patch.object(bot,"load_tickers_from_file",return_value=list(tickers)), \
             patch.object(bot,"cached_history",return_value=raw):
            return bot.voer_lijst_uit("list","01","Test",status=status)

    def test_all_six_strategies_and_no_fake_portfolio(self):
        status={"analyses":0,"fouten":0,"overgeslagen":0}
        report=self.sector(recent_history(),status=status)
        self.assertEqual(status["analyses"],6)
        self.assertEqual(status["fouten"],0)
        self.assertNotIn("€100",report)
        self.assertIn("geen portefeuillewaarde",report)
        self.assertIn("MRA Traag P/L:",report)

    def test_partial_data_coverage(self):
        report=self.sector(recent_history(),tickers=("OK","MISSING"))
        self.assertIn("2 gevraagd | 1 geanalyseerd",report)
        self.assertIn("MISSING: geen koersdata",report)
        self.assertIn("STATUS: ONVOLLEDIG",report)

    def test_no_data_has_no_fake_profit_or_no_action_claim(self):
        report=self.sector(pd.DataFrame())
        self.assertIn("P/L: niet beschikbaar",report)
        self.assertIn("Geen betrouwbare uitkomst",report)

    def test_short_history_is_reported(self):
        report=self.sector(recent_history(count=100))
        self.assertIn("100 geldige koersrijen",report)

    def test_ticker_failure_does_not_leave_partial_strategy_results(self):
        status={"analyses":0,"fouten":0,"overgeslagen":0}
        original=bot.bereken_indicatoren_vectorized
        def broken(frame,fast,*args):
            if fast==9: raise ValueError("broken hyper")
            return original(frame,fast,*args)
        with patch.object(bot,"bereken_indicatoren_vectorized",side_effect=broken), \
             self.assertLogs(bot.logger,level="ERROR"):
            report=self.sector(recent_history(),status=status)
        self.assertEqual(status["analyses"],0)
        self.assertIn("broken hyper",report)
        self.assertIn("Traag (50/200) P/L: niet beschikbaar",report)

    def run_main(self, mail=True, failure=False):
        def sector(*args,**kwargs):
            if failure: raise ValueError("sector broken")
            kwargs["status"]["analyses"]+=6
            return "Sector\n"
        with patch.object(bot,"voer_lijst_uit",side_effect=sector), \
             patch.object(bot.time,"sleep"), \
             patch.object(bot,"send_email",return_value=mail) as sender:
            result=bot.main()
        return result,sender.call_args

    def test_mail_success_confirmed_telegram_absent(self):
        with self.assertLogs(bot.logger,level="INFO") as logs:
            code,_=self.run_main()
        self.assertEqual(code,0)
        self.assertTrue(any("E-mail succesvol verstuurd" in s for s in logs.output))
        self.assertFalse(hasattr(bot,"send_telegram"))

    def test_mail_failure_fails_job(self):
        with self.assertLogs(bot.logger,level="ERROR"):
            code,_=self.run_main(mail=False)
        self.assertEqual(code,1)

    def test_sector_error_is_in_email_and_exit_status(self):
        with self.assertLogs(bot.logger,level="ERROR"):
            code,args=self.run_main(failure=True)
        self.assertEqual(code,1)
        self.assertIn("[ONVOLLEDIG]",args.args[0])
        self.assertIn("Hoogland: MISLUKT",args.args[1])


if __name__=="__main__":
    unittest.main()
