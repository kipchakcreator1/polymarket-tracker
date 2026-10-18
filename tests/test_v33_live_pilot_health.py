"""Regression checks for 350-wallet monitor telemetry, no network."""
import unittest
from tools import v33_live_pilot_health as h

NOW=1792318000
A="0x"+"a"*40
B="0x"+"b"*40
C="0x"+"c"*40
D="0x"+"d"*40


def selector(source,now):
    return {A:{},B:{}} if h.bot.MAX_WALLETS==300 else {
        A:{},B:{},C:{},D:{}}


def fixture():
    shortlist={"schema":"v33_wallet_shortlist_1",
               "observed_at":NOW-100,"shortlist":[]}
    state={"version":33,
           "cursors":{A:NOW-1,B:NOW-1,C:NOW-1,D:NOW-1},
           "last_run":{"watching":4,"request_errors":0,"poll_gaps":0,
                       "new_fills":5,"alerts":1,"open_position_alerts":0,
                       "this_poll":{"eligible_source_buy_sports":2,
                                    "book_verified_sports":1,
                                    "major_unredeemed_loss_cluster_90d":1}},
           "open_sent":{"some-entry":NOW-5},
           "sent":{C+"|market_1|token":NOW-50,
                   B+"|market_2|token":NOW-100},
           "shadow_evidence":{
               C+"|cond1|token":{"quoted_at":NOW-30,
                                 "decision":"blocked_recent"},
               D+"|cond2|token":{"quoted_at":NOW-40,
                                 "decision":"sent_telegram"},
               B+"|cond3|token":{"quoted_at":NOW-200,
                                 "decision":"blocked_recent"},
               D+"|old|token":{"quoted_at":NOW-30*3600,
                                "decision":"sent_telegram"}}}
    return shortlist,state


class LivePilotHealthTests(unittest.TestCase):
    def test_group_separation_and_correct_live_inventory(self):
        source,state=fixture()
        r=h.build(source,state,NOW,selector)
        self.assertTrue(r["last_saved_exactly_matches_reconstructed_350"])
        self.assertEqual(r["source_reconstructed_old_count"],2)
        self.assertEqual(r["new_research_group_present_in_saved_runtime"],2)
        new=r["by_reconstructed_cohort"]["additional_up_to_50"]
        old=r["by_reconstructed_cohort"]["old_300_reconstructed"]
        self.assertEqual(new["prospective_book_quote_decisions"]["1h"]
                         ["quoted_observations"],2)
        self.assertEqual(new["prospective_book_quote_decisions"]["1h"]
                         ["marked_rejected_recent_form"],1)
        self.assertEqual(new["prospective_book_quote_decisions"]["1h"]
                         ["marked_telegram_sent"],1)
        self.assertEqual(old["prospective_book_quote_decisions"]["1h"]
                         ["quoted_observations"],1)
        self.assertEqual(new["recent_buy_dedupe_keys"]["1h"]["saved_buy_dedupe_keys"],1)
        self.assertEqual(r["last_poll_suppression_deltas"]
                         ["major_unredeemed_loss_cluster_90d"],1)
        self.assertTrue(r["no_api_requests_or_production_writes"])

    def test_missing_live_cursors_not_falsely_called_pilot_active(self):
        source,state=fixture()
        state["cursors"].pop(C)
        state["last_run"]["watching"]=3
        r=h.build(source,state,NOW,selector)
        self.assertFalse(r["last_saved_exactly_matches_reconstructed_350"])
        self.assertEqual(r["new_research_group_present_in_saved_runtime"],1)

    def test_stale_or_future_quote_excluded_without_fake_fills(self):
        source,state=fixture()
        state["shadow_evidence"][D+"|cond2|token"]["quoted_at"]=NOW+100000
        r=h.build(source,state,NOW,selector)
        new=r["by_reconstructed_cohort"]["additional_up_to_50"]
        self.assertEqual(new["prospective_book_quote_decisions"]["1h"]
                         ["marked_telegram_sent"],0)
        self.assertTrue(r["source_market_pnl_and_outcome_not_verified"])

    def test_old_shortlist_not_strict_subset_fails_closed(self):
        source,state=fixture()
        def bad(_,__):
            return {A:{},B:{}} if h.bot.MAX_WALLETS==300 else {A:{},C:{}}
        with self.assertRaises(ValueError):
            h.build(source,state,NOW,bad)

    def test_original_cap_restored_after_reconstruction(self):
        source,state=fixture()
        saved=h.bot.MAX_WALLETS
        h.build(source,state,NOW,selector)
        self.assertEqual(h.bot.MAX_WALLETS,saved)

    def test_invalid_runtime_state_rejected(self):
        source,state=fixture()
        state["version"]=32
        with self.assertRaises(ValueError):
            h.build(source,state,NOW,selector)


if __name__=="__main__":
    unittest.main()
