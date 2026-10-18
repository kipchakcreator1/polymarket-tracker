"""Regression checks for research-only queue; no network or Telegram."""
import unittest
from tools import v33_research_shadow_queue as queue

A = "0x" + "a"*40
B = "0x" + "b"*40
C = "0x" + "c"*40
D = "0x" + "d"*40


def inputs():
    legacy = {"schema": "v33_wallet_shortlist_1", "observed_at": 100,
              "shortlist": [
                  {"wallet": A, "closed": 100,
                   "profitable_closed_rate": .99, "realized_closed_pnl": 5000,
                   "profit_factor": 50},
                  {"wallet": B, "closed": 100,
                   "profitable_closed_rate": .80, "realized_closed_pnl": 1000,
                   "profit_factor": 2},
                  {"wallet": C, "closed": 40,
                   "profitable_closed_rate": .75, "realized_closed_pnl": 105,
                   "profit_factor": 1.2},
              ]}
    evidence = {"schema": "v33_evidence_rank_1", "source_updated_at": 110,
                "ranked_for_research_only": [
                    {"wallet": A, "distinct_verified_sports_events": 30,
                     "history_evidence_complete": False, "status": "OBSERVATIONS_ONLY",
                     "blocking_reasons": ["incomplete_closed_redeemable_positions",
                                          "severe_unredeemed_loss_inventory"],
                     "by_league": {"UFC": {"wins": 30, "losses": 0}}},
                    {"wallet": B, "distinct_verified_sports_events": 8,
                     "history_evidence_complete": False, "status": "INSUFFICIENT",
                     "blocking_reasons": ["truncated_markets"]},
                    {"wallet": D, "distinct_verified_sports_events": 3,
                     "history_evidence_complete": False, "status": "INSUFFICIENT",
                     "blocking_reasons": ["unverified_final_outcomes"]},
                ]}
    cohort = {"schema": "v33_wallet_copy_cohort_1",
              "input_truth_updated_at": 105, "wallets": [
                  {"wallet": B, "status": "OBSERVED_POSITIVE_PRICE_PROXY",
                   "roi_if_5pct_worse_entry_5m_no_fees": 4.0,
                   "historical_data_warnings": ["uncertified_outcome_sample"],
                   "copy_roi_verified": False}]}
    forward = {"schema": "v33_forward_net_paper_1",
               "official_source_updated_at": 115, "positions": []}
    return legacy,evidence,cohort,forward


def result(risk=None, limit=300):
    return queue.build_report(*inputs(), risk=risk, limit=limit)


class ResearchQueueTests(unittest.TestCase):
    def test_strong_old_winrate_does_not_override_hidden_loss_inventory(self):
        r = result()
        rows = {x["wallet"]:x for x in r["candidate_rows"]}
        self.assertEqual(rows[A]["research_status"], "RISK_REVIEW")
        self.assertFalse(rows[A]["live_approved"])
        self.assertFalse(rows[A]["sports_history_complete"])
        self.assertEqual(r["live_approved_count"], 0)
        self.assertNotIn("LIVE_APPROVED", r["status_counts"])

    def test_positive_price_proxy_does_not_make_buy_eligible(self):
        r = result()
        rows = {x["wallet"]:x for x in r["candidate_rows"]}
        self.assertEqual(rows[B]["historical_copy_proxy_not_executable_roi"], 4.0)
        self.assertFalse(rows[B]["net_follower_roi_verified"])
        self.assertFalse(rows[B]["live_approved"])
        self.assertIn("uncertified_outcome_sample", rows[B]["blocking_reasons"])

    def test_unknown_wallet_is_not_counted_as_zero_losses(self):
        r = result()
        rows = {x["wallet"]:x for x in r["candidate_rows"]}
        self.assertEqual(rows[C]["sports_evidence_status"], "NOT_AUDITED")
        self.assertIn("no_independent_sports_event_audit", rows[C]["blocking_reasons"])
        self.assertIsNone(rows[C]["observed_loss_inventory_major"])
        self.assertFalse(rows[C]["recent_risk_page_audited"])

    def test_partial_risk_pages_never_certify_ledger(self):
        risk = {"captured_at": 123, "samples": [
            {"wallet_prefix": B[:14], "pages": {
                "CLOSED": {"pagination_complete": False,
                           "timestamp_states": {"valid": 250}},
                "REDEEMABLE_LOST": {"pagination_complete": True,
                                    "timestamp_states": {"valid": 9, "nonpositive": 1},
                                    "redeemable_major_loss_rows": 5}}}]}
        r = result(risk)
        rows = {x["wallet"]:x for x in r["candidate_rows"]}
        b = rows[B]
        self.assertEqual(b["research_status"], "RISK_REVIEW")
        self.assertIn("partial_v2_closed", b["blocking_reasons"])
        self.assertIn("v2_position_activity_timestamp_unknown", b["blocking_reasons"])
        self.assertFalse(b["recent_risk_page_is_full_ledger"])
        self.assertFalse(b["live_approved"])

    def test_duplicate_condition_does_not_create_30_profitable_copies(self):
        _,_,_,f = inputs()
        one = {"wallet": D, "condition": "cond",
               "status": "verified_settled_paper", "stream": "shadow_only",
               "final_outcome": "win", "real_trading_fill": False,
               "stake_usd": 5, "hypothetical_pnl_usd": {
                   "pnl_fee_0.05_plus_5pct_worse_entry": 2}}
        f["positions"] = [dict(one) for _ in range(30)]
        r = queue.build_report(*inputs()[:3], f)
        d = {x["wallet"]:x for x in r["candidate_rows"]}[D]
        stats = d["forward_book_quote_paper"]
        self.assertEqual(stats["ambiguous_same_condition_observations"], 30)
        self.assertEqual(stats["distinct_quoted_conditions_settled"], 0)
        self.assertFalse(d["forward_model_sample_sufficient"])

    def test_forward_model_cannot_be_mislabeled_follower_roi(self):
        _,_,_,f = inputs()
        f["positions"] = [
            {"wallet": B, "condition": "cond" + str(i),
             "status": "verified_settled_paper", "stream": "telegram_sent",
             "final_outcome": "win", "real_trading_fill": False,
             "stake_usd": 5, "hypothetical_pnl_usd": {
                 "pnl_fee_0.05_plus_5pct_worse_entry": 1}}
            for i in range(30)]
        r = queue.build_report(*inputs()[:3], f)
        b = {x["wallet"]:x for x in r["candidate_rows"]}[B]
        self.assertTrue(b["forward_model_sample_sufficient"])
        self.assertIsNone(b["forward_book_quote_paper"]["actual_net_copy_roi"])
        self.assertFalse(b["net_follower_roi_verified"])
        self.assertFalse(b["live_approved"])

    def test_queue_is_bounded_and_deterministic_without_using_old_winrate(self):
        r1 = result(limit=2)
        r2 = result(limit=2)
        self.assertEqual(r1["candidate_rows"], r2["candidate_rows"])
        self.assertEqual(r1["research_queue_count"], 2)
        self.assertEqual(r1["research_population"], 4)
        self.assertEqual(r1["snapshot_legacy_baseline_count"], 3)
        self.assertFalse(r1["baseline_is_exact_live_inventory"])
        self.assertEqual(r1["candidate_rows"][0]["wallet"], A)

    def test_invalid_data_fails_closed(self):
        legacy,evidence,cohort,forward = inputs()
        legacy["schema"] = "arbitrary"
        with self.assertRaises(ValueError):
            queue.build_report(legacy,evidence,cohort,forward)
        with self.assertRaises(ValueError):
            result(limit=301)

    def test_risk_prefix_collision_does_not_assign_risk_to_wrong_wallet(self):
        legacy,evidence,cohort,forward = inputs()
        wallet2 = A[:-2] + "bb"
        legacy["shortlist"].append({"wallet": wallet2, "closed": 31,
           "profitable_closed_rate": .9, "realized_closed_pnl": 1000,
           "profit_factor": 2})
        risk = {"samples": [{"wallet_prefix": A[:14], "pages": {
            "REDEEMABLE_LOST": {"redeemable_major_loss_rows": 20}}}]}
        r = queue.build_report(legacy,evidence,cohort,forward,risk)
        x = {v["wallet"]:v for v in r["candidate_rows"]}
        self.assertFalse(x[A]["recent_risk_page_audited"])
        self.assertFalse(x[wallet2]["recent_risk_page_audited"])


if __name__ == "__main__":
    unittest.main()
