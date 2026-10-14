"""Offline checks for v3.2 cached-wallet score migration; zero API calls."""
import unittest

import smart_tracker_v2 as scoring


class ScoreMigrationTests(unittest.TestCase):
    def test_old_qualified_wallet_missing_pf_is_rescored_even_if_fresh(self):
        now = 2000000000
        meta = {
            "checked_at": now - 10,
            "n": 100, "winrate": 0.91, "recent_realized_pnl": 500,
            "lb_pnl": 600,
        }
        self.assertTrue(scoring.missing_copy_metrics(meta))
        self.assertEqual(scoring.wallets_due_for_scoring({"wallet": meta}, now), ["wallet"])

    def test_null_profit_factor_is_known_not_missing(self):
        now = 2000000000
        meta = {
            "checked_at": now - 10,
            "n": 100, "winrate": 1.0, "recent_realized_pnl": 5000,
            "profit_factor": None,
        }
        self.assertFalse(scoring.missing_copy_metrics(meta))
        self.assertEqual(scoring.wallets_due_for_scoring({"already": meta}, now), [])

    def test_low_quality_wallet_not_refreshed_early(self):
        now = 2000000000
        meta = {
            "checked_at": now - 10,
            "n": 100, "winrate": 0.20, "recent_realized_pnl": -9000,
        }
        self.assertFalse(scoring.missing_copy_metrics(meta))
        self.assertEqual(scoring.wallets_due_for_scoring({"loser": meta}, now), [])

    def test_legacy_qualified_first_then_stale_and_bounded(self):
        now = 2000000000
        wallets = {
            "stale": {
                "checked_at": now - 900000, "n": 10, "winrate": .1,
                "recent_realized_pnl": -100,
            },
            "qualified": {
                "checked_at": now - 10, "n": 100, "winrate": .90,
                "recent_realized_pnl": 150, "lb_pnl": 100,
            },
            "current": {
                "checked_at": now - 10, "n": 100, "winrate": .90,
                "recent_realized_pnl": 150, "profit_factor": 2.0,
            }
        }
        self.assertEqual(scoring.wallets_due_for_scoring(wallets, now, limit=1), ["qualified"])
        self.assertEqual(scoring.wallets_due_for_scoring(wallets, now, limit=3), ["qualified", "stale"])


if __name__ == "__main__":
    unittest.main()
