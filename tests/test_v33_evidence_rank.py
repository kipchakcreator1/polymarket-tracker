"""The report must never call incomplete skill data a certified winrate."""
import unittest
from tools import v33_evidence_rank as rank

W = "0x" + "a"*40


def wallet(n=30, wins=21):
    items = [
        {"condition": "0x"+format(i+1,"064x"),
         "event_slug": f"ufc-fight-night-{i}",
         "league": "UFC", "resolved_at": 1800000000+i,
         "direction": "win" if i < wins else "loss",
         "source_first_buy_price": .62}
        for i in range(n)]
    return {"audited_at": 1800002000,
            "trade_window_complete": True,
            "market_scan_complete": True,
            "certified_sample": True,
            "resolved_sports_sample": n,
            "unverified_final_or_mapping_count": 0,
            "position_truth": {"certified_sample": True,
                "position_history_complete": True,
                "position_market_scan_complete": True,
                "events": items, "position_accounting": {
                     "unredeemed_major_losers_100usd": 0}}}


class EvidenceRankingTests(unittest.TestCase):
    def test_wilson_penalizes_small_samples(self):
        self.assertLess(rank.wilson_lower(10,10), .90)
        self.assertLess(rank.wilson_lower(21,30), .70)
        self.assertIsNone(rank.wilson_lower(0,0))

    def test_clean_30_ufc_events_are_historical_only(self):
        value = rank.wallet_evidence(W, wallet(), [])
        self.assertEqual(value["distinct_verified_sports_events"], 30)
        self.assertTrue(value["history_evidence_complete"])
        self.assertEqual(value["status"], "HISTORICAL_AUDIT_READY")
        self.assertEqual(value["by_league"]["UFC"]["wins"], 21)
        self.assertEqual(value["by_league"]["UFC"]["losses"], 9)
        self.assertEqual(value["by_league"]["UFC"]["source_first_buy_price_covered"],30)
        self.assertIsNotNone(value["by_league"]["UFC"]["source_first_buy_hold_roi_no_fees"])
        self.assertFalse(value["copy_roi_verified"])
        self.assertFalse(value["live_ranking_approved"])

    def test_duplicate_match_markets_do_not_count_as_two_wins(self):
        data = wallet()
        data["position_truth"]["events"][1]["event_slug"] = "ufc-fight-night-0"
        value = rank.wallet_evidence(W,data,[])
        self.assertEqual(value["distinct_verified_sports_events"],28)
        self.assertFalse(value["history_evidence_complete"])
        self.assertIn("event_deduplication_ambiguity",value["blocking_reasons"])

    def test_old_reports_without_event_id_cannot_claim_certification(self):
        data = wallet()
        for row in data["position_truth"]["events"]:
            del row["event_slug"]
        value=rank.wallet_evidence(W,data,[])
        self.assertEqual(value["distinct_verified_sports_events"],0)
        self.assertFalse(value["history_evidence_complete"])
        self.assertIn("event_deduplication_ambiguity",value["blocking_reasons"])

    def test_unknown_final_results_block_30_of_30_selection_bias(self):
        data=wallet(30,30)
        data["unverified_final_or_mapping_count"]=31
        value=rank.wallet_evidence(W,data,[])
        self.assertIn("unverified_final_outcomes",value["blocking_reasons"])
        self.assertFalse(value["history_evidence_complete"])

    def test_archived_major_losses_require_review(self):
        data=wallet()
        data["position_truth"]["position_accounting"]={
            "unredeemed_major_losers_100usd":18}
        value=rank.wallet_evidence(W,data,[])
        self.assertFalse(value["history_evidence_complete"])
        self.assertIn("severe_unredeemed_loss_inventory",value["blocking_reasons"])

    def test_positive_paper_hold_without_actual_fills_is_not_copy_roi(self):
        rows=[{"wallet":W,"result":"win","paper_hold_pnl_no_fees":1.0,
               "paper_stake_usd":5} for i in range(30)]
        value=rank.wallet_evidence(W,wallet(),rows)
        self.assertEqual(value["forward_paper_settled"],30)
        self.assertTrue(value["forward_paper_evidence_sufficient"])
        self.assertFalse(value["copy_roi_verified"])
        self.assertFalse(value["live_ranking_approved"])

    def test_no_one_approved_by_report_builder(self):
        source={"updated_at":1800000000,
                "wallets":{W:wallet()},"alerts":{"rows":[]}}
        report=rank.build_report(source)
        self.assertEqual(report["n_wallets_audited"],1)
        self.assertEqual(report["n_historical_audit_ready"],1)
        self.assertEqual(report["n_live_approved"],0)

    def test_missing_trade_or_position_history_cannot_pass(self):
        data=wallet()
        data["trade_window_complete"]=False
        data["position_truth"]["position_history_complete"]=False
        result=rank.wallet_evidence(W,data,[])
        self.assertIn("incomplete_maker_taker_trade_window", result["blocking_reasons"])
        self.assertIn("incomplete_closed_redeemable_positions",result["blocking_reasons"])
        self.assertFalse(result["history_evidence_complete"])


if __name__ == "__main__":
    unittest.main()
