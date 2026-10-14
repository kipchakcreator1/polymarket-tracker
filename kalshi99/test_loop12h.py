"""Offline timing safety tests for a continuous hosted-runner 12h experiment."""
import unittest

from kalshi99 import loop12h as runner
from kalshi99.experiment12h import START, END, CADENCE_SECONDS


class Continuous12hTests(unittest.TestCase):
    def test_next_tick_fixed_alignment(self):
        beginning = START.timestamp()
        self.assertEqual(runner.next_scan_epoch(beginning), beginning + 300)
        self.assertEqual(runner.next_scan_epoch(beginning + 11), beginning + 300)
        self.assertEqual(runner.next_scan_epoch(beginning + 304), beginning + 600)
        self.assertEqual(runner.next_scan_epoch(beginning - 1), beginning)

    def test_handoff_earlier_than_six_hour_timeout(self):
        started = START.timestamp()
        handoff = started + runner.SEGMENT_SECONDS - runner.HANDOFF_RESERVE_SECONDS
        self.assertEqual(runner.decision(started, started), "scan")
        self.assertEqual(runner.decision(handoff, started), "handoff")
        self.assertLess(runner.SEGMENT_SECONDS, 6 * 3600)

    def test_final_wins_over_handoff(self):
        started = END.timestamp() - 24 * 3600
        self.assertEqual(runner.decision(END.timestamp(), started), "finalize")
        self.assertEqual(runner.decision(END.timestamp() + 300, started), "finalize")

    def test_no_extra_experiment_time(self):
        self.assertEqual(int((END - START).total_seconds()), 12 * 3600)
        self.assertEqual(CADENCE_SECONDS, 300)

    def test_runner_modules_are_paper_only(self):
        self.assertEqual(runner.SCAN_MODULE, "kalshi99.experiment12h")
        self.assertEqual(runner.TELEGRAM_MODULE, "kalshi99.experiment12h_telegram")
        self.assertEqual(runner.RESEARCH_BRANCH, "kalshi-12h-research")


if __name__ == "__main__":
    unittest.main()
