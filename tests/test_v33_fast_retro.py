"""Historical first-BUY hold ROI must not be labeled actual follower profits."""
import unittest
from tools import v33_fast_retro as retro

W = "0x" + "a"*40

def pick(n, result, price=.6, slug=None):
    return {"condition": "0x" + format(n+1, "064x"),
            "event_slug": slug if slug is not None else f"ufc-fight-night-{n}",
            "league": "UFC", "resolved_at": 1900000000+n,
            "direction": result, "source_first_buy_price": price}

def wallet(events, unknown=0):
    return {"certified_sample": True, "trade_window_complete": True,
            "market_scan_complete": True,
            "unverified_final_or_mapping_count": unknown,
            "position_truth": {"certified_sample": True,
                "position_history_complete": True,
                "position_market_scan_complete": True, "events": events,
                "position_accounting":
                    {"unredeemed_major_losers_100usd": 18}}}

class TestFastRetro(unittest.TestCase):
    def test_priced_winning_and_losing_buys_not_follower_copy_roi(self):
        r=retro.analyze_wallet(W,wallet([pick(0,"win",.5),
                                         pick(1,"loss",.75)]))
        u=r["by_league"]["UFC"]
        self.assertAlmostEqual(u["source_first_buy_hold_roi_no_fees"],0)
        self.assertEqual(u["wins"],1)
        self.assertIsNone(u["paper_copy_roi"])
        self.assertFalse(r["historical_winrate_certified"])

    def test_missing_market_prevents_certified_and_has_stress_range(self):
        events=[pick(i,"win" if i<21 else "loss") for i in range(30)]
        result=retro.analyze_wallet(W,wallet(events,unknown=31))
        self.assertEqual(result["by_league"]["UFC"]["wins"],21)
        self.assertEqual(result["by_league"]["UFC"]["losses"],9)
        self.assertAlmostEqual(
            result["stress_scenario_if_all_ambiguous_are_losses"],21/61,
            places=4)
        self.assertFalse(result["historical_winrate_certified"])
        self.assertEqual(result["large_unredeemed_losses"],18)

    def test_multiple_markets_same_event_excluded(self):
        events=[pick(0,"win"),pick(1,"loss",slug="ufc-fight-night-0")]
        r=retro.analyze_wallet(W,wallet(events))
        self.assertEqual(r["verified_unique_events"],0)
        self.assertEqual(r["missing_or_duplicate_event_ids"]
                         ["multiple_markets_per_event"],1)

    def test_missing_source_entry_price_disables_roi(self):
        events=[pick(i,"win",.6) for i in range(30)]
        events[0]["source_first_buy_price"]=None
        r=retro.analyze_wallet(W,wallet(events))
        self.assertEqual(r["verified_unique_events"],30)
        self.assertIsNone(r["by_league"]["UFC"]
                          ["source_first_buy_hold_roi_no_fees"])
        self.assertFalse(r["by_league"]["UFC"]
                         ["source_buy_price_coverage_complete"])

    def test_true_certification_requires_history_and_event_ids(self):
        events=[pick(i,"win",.6) for i in range(30)]
        data=wallet(events)
        result=retro.analyze_wallet(W,data)
        self.assertTrue(result["historical_winrate_certified"])
        data["trade_window_complete"]=False
        self.assertFalse(retro.analyze_wallet(W,data)["historical_winrate_certified"])

if __name__=="__main__":
    unittest.main()
