"""Research-only durable audit checkpoint: no real API calls in tests."""
import unittest
from unittest import mock

from tools import v33_shadow_checkpoint as mod
from tools import v33_result_audit as truth

NOW = 1792307000
W = "0x" + "a"*40
C = "0x" + "b"*64
T = "123"
K = W + "|" + C + "|" + T


def live(stamp=NOW-50, decision="blocked_recent"):
    return {
        "topic": "sports", "decision": decision,
        "quoted_at": stamp, "book_quoted_at": stamp,
        "source_trade_at": stamp - 30,
        "hypothetical_copy_ask": .5,
        "hypothetical_copy_stake_usd": 5,
        "hypothetical_copy_shares": 10,
    }


def inputs(live_quotes=None, previous_truth=None):
    state = {"version": 33, "shadow_evidence": live_quotes or {}}
    truth_report = {"schema": "v33_true_sports_audit_1",
                    "updated_at": NOW, "shadows": {"rows": previous_truth or []}}
    return state, truth_report


class DurableCheckpointTests(unittest.TestCase):
    def test_two_runs_persist_quote_then_resolve_after_ring_drops_it(self):
        state, report = inputs({K: live()})
        with mock.patch.object(truth.time, "time", return_value=NOW), \
             mock.patch.object(truth, "classify",
                               return_value=("unknown_resolution_pending", None)):
            one = mod.build_checkpoint(None, state, report, now=NOW, max_checks=1)
        self.assertEqual(len(one["rows"]), 1)
        self.assertEqual(one["rows"][0]["shadow_decision"], "blocked_recent")
        self.assertEqual(one["rows"][0]["original_book_quote"]["hypothetical_copy_ask"], .5)
        self.assertEqual(one["meta"]["newly_final_classified_this_run"], 0)
        self.assertTrue(one["meta"]["first_run_history_unknown"])
        self.assertTrue(one["meta"]["coverage_breach_or_unknown"])

        # The public signal ring evicts the bet. Keep its first observed ask.
        later = NOW + 3700
        empty_state, empty_truth = inputs({})
        with mock.patch.object(truth.time, "time", return_value=later), \
             mock.patch.object(truth, "classify", return_value=("win", NOW+3500)):
            two = mod.build_checkpoint(one, empty_state, empty_truth,
                                       now=later, max_checks=1)
        self.assertEqual(two["meta"]["completed_runs"], 2)
        self.assertEqual(two["meta"]["newly_final_wins"], 1)
        self.assertEqual(two["rows"][0]["original_book_quote"]["book_quoted_at"],
                         NOW-50)
        self.assertEqual(two["rows"][0]["shadow_decision"], "blocked_recent")
        self.assertEqual(two["rows"][0]["result"], "win")
        self.assertAlmostEqual(two["rows"][0]["paper_hold_pnl_no_fees"], 5.)

        later2 = later + 50
        with mock.patch.object(truth.time, "time", return_value=later2), \
             mock.patch.object(truth, "classify") as classifier:
            three = mod.build_checkpoint(two, empty_state, empty_truth,
                                         now=later2, max_checks=1)
        classifier.assert_not_called()
        self.assertEqual(three["meta"]["newly_final_wins"], 0)
        self.assertEqual(three["rows"][0]["result"], "win")

    def test_terminal_outcome_conflict_is_flagged_not_rewritten(self):
        base={"wallet":W,"condition":C,"token_id":T,
              "alert_at":NOW-200,"result":"win",
              "shadow_decision":"blocked_recent"}
        historical=dict(base,result="loss")
        old={"schema":mod.SCHEMA,"last_saved_at":NOW-100,
             "rows":[base],"meta":{"completed_runs":2}}
        _,ignored,collisions=mod.merge_rows(old["rows"],[historical],NOW)
        self.assertEqual(len(collisions),1)
        self.assertEqual(ignored["capacity_eviction"],0)
        with mock.patch.object(truth.time,"time",return_value=NOW), \
             mock.patch.object(truth,"classify") as classifier:
            checkpoint=mod.build_checkpoint(old,*inputs({},[historical]),
                                            now=NOW,max_checks=1)
        classifier.assert_not_called()
        self.assertEqual(checkpoint["meta"]["terminal_outcome_conflicts"],1)
        self.assertEqual(checkpoint["rows"][0]["result"],"win")
        self.assertTrue(checkpoint["meta"]["coverage_breach_or_unknown"])

    def test_limited_capacity_is_reported_as_coverage_breach(self):
        records=[{"wallet":W,"condition":"0x"+format(i+1,"064x"),
                  "token_id":T,"alert_at":NOW-500+i,
                  "result":"loss"} for i in range(4)]
        with mock.patch.object(mod,"CONDITION_CAP",2):
            rows,ignored,_=mod.merge_rows(records,[],NOW)
        self.assertEqual(len(rows),2)
        self.assertEqual(ignored["capacity_eviction"],2)

    def test_ring_gap_is_flagged_not_assumed_complete(self):
        # Only last ~10 seconds are visible after 1,000 seconds offline.
        c2="0x"+"c"*64
        state, report=inputs({
            K:live(NOW-20),
            W+"|"+c2+"|"+T: live(NOW-10)
        })
        old={"schema":mod.SCHEMA,"last_saved_at":NOW-1000,
             "rows":[],"meta":{"completed_runs":1}}
        with mock.patch.object(truth.time,"time",return_value=NOW), \
             mock.patch.object(truth,"classify",
                               return_value=("unknown_resolution_pending",None)):
            checkpoint=mod.build_checkpoint(old,state,report,now=NOW,max_checks=1)
        self.assertTrue(checkpoint["meta"]["risk_of_unobserved_ring_evictions"])
        self.assertTrue(checkpoint["meta"]["coverage_breach_or_unknown"])
        self.assertEqual(checkpoint["meta"]["observed_new_quote_keys"],2)

    def test_existing_rolling_ring_keys_are_not_all_reported_as_new(self):
        old={"schema":mod.SCHEMA,"last_saved_at":NOW-800,
             "rows":[{"wallet":W,"condition":C,"token_id":T,
                      "result":"unknown_resolution_pending",
                      "checked_at":NOW-40, "alert_at":NOW-50,
                      "shadow_decision":"blocked_recent"}],
             "meta":{"completed_runs":1}}
        state,report=inputs({K:live()})
        with mock.patch.object(truth.time,"time",return_value=NOW), \
             mock.patch.object(truth,"classify",
                               return_value=("unknown_resolution_pending",None)):
            out=mod.build_checkpoint(old,state,report,now=NOW,max_checks=1)
        self.assertEqual(out["meta"]["observed_new_quote_keys"],0)
        self.assertEqual(out["meta"]["archived_rows"],1)
        self.assertEqual(out["meta"]["completed_runs"],2)

    def test_undecided_sample_never_fabricates_copy_roi(self):
        state,report=inputs({K:live()})
        with mock.patch.object(truth.time,"time",return_value=NOW), \
             mock.patch.object(truth,"classify",
                               return_value=("unknown_resolution_pending",None)):
            checkpoint=mod.build_checkpoint(None,state,report,now=NOW,max_checks=1)
        self.assertIsNone(checkpoint["meta"]["actual_copy_roi"])
        self.assertFalse(checkpoint["meta"]["unbiased_causal_filter_evaluation_possible"])
        self.assertEqual(checkpoint["meta"]["filled_order_count"],0)
        self.assertGreaterEqual(checkpoint["meta"]["not_yet_terminal"],1)

    def test_throughput_pressure_is_estimate_not_claim_of_complete_coverage(self):
        extra="0x"+"c"*64
        state,report=inputs({
            K: live(NOW-40),
            W+"|"+extra+"|"+T: live(NOW-30)
        })
        with mock.patch.dict(mod.os.environ,
                             {"V33_SHADOW_SCHEDULE_RUNS_PER_HOUR":"6"}), \
             mock.patch.object(truth.time,"time",return_value=NOW), \
             mock.patch.object(truth,"classify",
                               return_value=("unknown_resolution_pending",None)):
            result=mod.build_checkpoint(None,state,report,now=NOW,max_checks=1)
        self.assertEqual(result["meta"]["configured_max_checks_per_hour"],6)
        self.assertTrue(result["meta"]["estimated_incoming_exceeds_check_capacity"])
        self.assertIn("ring_rate_proxy_per_hour_not_verified_arrival_rate",
                      result["meta"])

    def test_ten_minute_schedule_detects_new_backlog_pressure(self):
        old={"schema":mod.SCHEMA,"last_saved_at":NOW-600,
             "rows":[],"meta":{"completed_runs":3,
                                "not_yet_terminal":0}}
        state,report=inputs({K:live()})
        with mock.patch.dict(mod.os.environ,
                             {"V33_SHADOW_SCHEDULE_RUNS_PER_HOUR":"6"}), \
             mock.patch.object(truth.time,"time",return_value=NOW), \
             mock.patch.object(truth,"classify",
                               return_value=("unknown_resolution_pending",None)):
            result=mod.build_checkpoint(old,state,report,now=NOW,max_checks=1)
        m=result["meta"]
        self.assertEqual(m["observed_new_quote_keys"],1)
        self.assertEqual(m["actual_new_quote_keys_per_hour_since_checkpoint"],6)
        self.assertEqual(m["nonterminal_backlog_change_from_previous_run"],1)
        self.assertTrue(m["nonterminal_backlog_grew"])

    def test_invalid_schema_or_budget_fails_closed(self):
        with self.assertRaises(ValueError):
            mod.safe_prev({"schema":"wrong","rows":[]})
        with self.assertRaises(ValueError):
            mod.build_checkpoint(None,{"version":32},{})
        with self.assertRaises(ValueError):
            mod.build_checkpoint(None,*inputs(),max_checks=100)

    def test_missing_quote_keeps_unknown_price_after_resolution(self):
        row={"wallet":W,"condition":C,"token_id":T,"alert_at":NOW-300,
             "result":"not_checked","shadow_decision":"blocked_recent"}
        state,report=inputs({},[row])
        with mock.patch.object(truth.time,"time",return_value=NOW), \
             mock.patch.object(truth,"classify",return_value=("win",NOW)):
            checkpoint=mod.build_checkpoint(None,state,report,now=NOW,max_checks=1)
        result=checkpoint["rows"][0]
        self.assertEqual(result["result"],"win")
        self.assertNotIn("original_book_quote",result)
        self.assertNotIn("paper_hold_pnl_no_fees",result)
        self.assertEqual(checkpoint["meta"]["missing_original_quote_rows"],1)


if __name__ == "__main__":
    unittest.main()
