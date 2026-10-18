"""Offline forward-copyability audit regression tests."""
import unittest
from tools import v33_copyability_validation as mod
from tools import v33_forward_net_paper as paper

W = "0x" + "a"*40
T = "123"


def condition(i):
    return "0x" + format(i, "064x")


def make_row(i, stream="telegram_sent", outcome="win", ask=.5,
             status="verified_settled_paper", wallet=W):
    pnl = {}
    for name, fee, mult in (
        ("pnl_fee_rate_0.00", 0, 1),
        ("pnl_fee_rate_0.05", .05, 1),
        ("pnl_fee_rate_0.07", .07, 1),
        ("pnl_fee_0.05_plus_5pct_worse_entry", .05, 1.05),
    ):
        pnl[name] = round(paper.paper_pnl(5, ask, outcome == "win", fee, mult), 5)
    return {"wallet": wallet, "condition": condition(i),
            "token_id": T, "stream": stream, "status": status,
            "final_outcome": outcome,
            "hypothetical_pnl_usd": pnl,
            "ask_vwap": ask, "stake_usd": 5,
            "visible_book_shares": 5/ask,
            "book_quoted_at": 1000+i*30,
            "real_trading_fill": False,
            "decision": "sent_telegram" if stream=="telegram_sent" else "blocked_recent"}


def fixture(rows, forward_only=True):
    return {"schema":"v33_forward_net_paper_1",
            "forward_only_no_backfilled_quotes": forward_only,
            "positions":rows, "official_source_updated_at": 2000}


class ValidationTests(unittest.TestCase):
    def test_known_three_settled_signals_are_model_loss_not_real_roi(self):
        rows = [make_row(1, outcome="loss", ask=.47),
                make_row(2, outcome="loss", ask=.62),
                make_row(3, outcome="win", ask=.57)]
        got = mod.build_report(fixture(rows), now=2001)
        sent = got["descriptive_by_stream"]["telegram_sent"]
        self.assertEqual(sent["winning_conditions"], 1)
        self.assertEqual(sent["losing_conditions"], 2)
        self.assertEqual(sent["distinct_market_conditions_with_observed_settlement"], 3)
        self.assertAlmostEqual(
            sent["scenario_results"]["fee_5pct"]["model_hold_pnl_usd"], -6.41667,
            places=4)
        self.assertFalse(sent["min_30_distinct_market_conditions"])
        self.assertFalse(got["paired_stream_comparison_ready"])
        self.assertIsNone(got["risk_gate_hypothetical_false_negative_rate"])
        self.assertIsNone(sent["actual_copy_net_roi"])

    def test_897_pending_rejects_are_not_marked_winners_or_losers(self):
        rows = [make_row(i, "shadow_only", "win",
                         status="awaiting_independent_official_settlement")
                for i in range(1,898)]
        got = mod.build_report(fixture(rows))
        shadow = got["descriptive_by_stream"]["shadow_only"]
        self.assertEqual(shadow["distinct_market_conditions_with_observed_settlement"],0)
        self.assertIsNone(shadow["scenario_results"]["fee_5pct"]["model_hold_roi"])
        self.assertEqual(got["raw_status_counts"]["awaiting_independent_official_settlement"],897)
        self.assertIn("no_unique_resolved_rejected_paper",got["blocking_reasons"])

    def test_independent_condition_sample_threshold_is_noncausal(self):
        rows = [make_row(i, "telegram_sent", "win") for i in range(1,31)]
        rows += [make_row(i, "shadow_only", "loss") for i in range(31,61)]
        got = mod.build_report(fixture(rows))
        self.assertTrue(got["paired_stream_comparison_ready"])
        self.assertFalse(got["causal_impact_of_filter_estimated"])
        self.assertEqual(got["wallets_live_approved"],0)
        self.assertFalse(got["descriptive_by_stream"]["telegram_sent"]
                         ["true_independent_sports_events_certified"])

    def test_same_market_cannot_count_multiple_wallet_outcomes(self):
        a = make_row(1)
        b = make_row(1, "shadow_only", "win", wallet="0x"+"b"*40)
        got = mod.build_report(fixture([a,b]))
        self.assertEqual(got["duplicated_market_quote_rows_excluded"],2)
        self.assertEqual(got["descriptive_by_stream"]["telegram_sent"]
                         ["distinct_market_conditions_with_observed_settlement"],0)

    def test_model_scenario_tampering_is_rejected(self):
        row = make_row(1)
        row["hypothetical_pnl_usd"]["pnl_fee_rate_0.05"] = 500
        got = mod.build_report(fixture([row]))
        self.assertEqual(got["data_quality_errors"]["scenario_inconsistent_with_quote"],1)
        self.assertEqual(got["validated_settled_quote_rows"],0)

    def test_not_explicitly_unfilled_cannot_count_as_paper(self):
        row = make_row(1)
        row["real_trading_fill"] = True
        got = mod.build_report(fixture([row]))
        self.assertEqual(got["data_quality_errors"]["not_explicitly_unfilled"],1)
        self.assertEqual(got["wallets_live_approved"],0)

    def test_missing_quote_evidence_does_not_impute_latency(self):
        got = mod.build_report(fixture([make_row(1)]),state={})
        self.assertIsNone(got["descriptive_by_stream"]["telegram_sent"]
                          ["source_trade_to_book_quote_latency_seconds_median"])

    def test_known_quote_source_latency_is_measured_not_guessed(self):
        row=make_row(1)
        key="|".join((row["wallet"],row["condition"],row["token_id"]))
        state={"signal_evidence": {key: {
            "source_trade_at":row["book_quoted_at"]-37,
            "book_quoted_at":row["book_quoted_at"]}}}
        got=mod.build_report(fixture([row]),state)
        self.assertEqual(got["descriptive_by_stream"]["telegram_sent"]
                         ["source_trade_to_book_quote_latency_seconds_median"],37)

    def test_impossible_history_quote_is_invalid_not_negative_latency(self):
        row=make_row(1)
        key="|".join((row["wallet"],row["condition"],row["token_id"]))
        state={"signal_evidence":{key:{
            "source_trade_at":row["book_quoted_at"]+400,
            "book_quoted_at":row["book_quoted_at"]}}}
        got=mod.build_report(fixture([row]),state)
        self.assertEqual(got["data_quality_errors"]["impossible_quote_chronology"],1)
        self.assertEqual(got["validated_settled_quote_rows"],0)

    def test_backfilled_prices_never_pass_as_forward(self):
        with self.assertRaises(ValueError):
            mod.build_report(fixture([make_row(1)], forward_only=False))

    def test_unpriced_stress_scenario_cannot_be_filled_with_zero(self):
        row = make_row(1,ask=.99)
        # 5% price deterioration is beyond $1; the scenario is NOT priced.
        got = mod.build_report(fixture([row]))
        self.assertEqual(got["data_quality_errors"]["scenario_unpriceable"],1)
        self.assertIsNone(got["descriptive_by_stream"]["telegram_sent"]
                          ["scenario_results"]["fee_5pct"]["model_hold_roi"])


if __name__=="__main__":
    unittest.main()
