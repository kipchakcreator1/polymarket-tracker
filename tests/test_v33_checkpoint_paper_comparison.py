"""Offline research-cohort tests; no API, Telegram or trading activity."""
import unittest
from tools import v33_checkpoint_paper_comparison as m
from tools.v33_forward_net_paper import paper_pnl

W="0x"+"a"*40
C="0x"+"b"*64
D="0x"+"c"*64
T="123"


def archived(c=C, result="win", quoted=110, price=.5, original=True):
    row={"wallet":W,"condition":c,"token_id":T,
         "alert_at":quoted,"settled_at":200,
         "result":result,"shadow_decision":"blocked_recent"}
    if original:
        row["original_book_quote"]={
            "topic":"sports", "no_fill_assumed":True,
            "hypothetical_copy_ask":price,
            "hypothetical_copy_stake_usd":5,
            "hypothetical_copy_shares":5/price,
            "source_trade_at":quoted-35,
            "book_quoted_at":quoted}
    return row


def sent(c=D,result="loss",price=.6):
    outcomes={
        "pnl_fee_rate_0.00":(0,1),
        "pnl_fee_rate_0.05":(.05,1),
        "pnl_fee_rate_0.07":(.07,1),
        "pnl_fee_0.05_plus_5pct_worse_entry":(.05,1.05)}
    return {"wallet":W,"condition":c,"token_id":T,
            "stream":"telegram_sent","status":"verified_settled_paper",
            "ask_vwap":price,"stake_usd":5,
            "visible_book_shares":5/price, "book_quoted_at":110,
            "final_outcome":result,"real_trading_fill":False,
            "hypothetical_pnl_usd":{
                name:round(paper_pnl(5,price,result=="win",fee,mul),5)
                for name,(fee,mul) in outcomes.items()}}


def checkpoint(rows):
    return {"schema":"v33_durable_shadow_checkpoint_1",
            "meta":{"completed_runs":4},"rows":rows}


def forward(rows):
    return {"schema":"v33_forward_net_paper_1",
            "forward_only_no_backfilled_quotes":True,"positions":rows}


class CohortComparisonTests(unittest.TestCase):
    def test_paper_only_comparison_preserves_distinct_groups_and_missing_causality(self):
        got=m.build_report(checkpoint([archived()]),forward([sent()]))
        a=got["by_cohort"]["blocked_recent"]
        b=got["by_cohort"]["telegram_sent"]
        self.assertEqual(a["observed_unique_market_conditions"],1)
        self.assertEqual(b["observed_unique_market_conditions"],1)
        self.assertEqual(a["wins"],1)
        self.assertEqual(b["losses"],1)
        self.assertAlmostEqual(a["scenario_models"]["fee_5pct"]
                               ["model_hold_pnl_usd"],4.75)
        self.assertAlmostEqual(b["scenario_models"]["fee_5pct"]
                               ["model_hold_pnl_usd"],-5)
        self.assertFalse(got["both_descriptive_samples_reach_30"])
        self.assertIsNone(got["causal_value_of_recent_form_gate"])
        self.assertIsNone(a["actual_copy_roi"])

    def test_no_quote_does_not_backfill_from_resolved_market(self):
        got=m.build_report(checkpoint([archived(original=False)]),forward([]))
        self.assertEqual(got["by_cohort"]["blocked_recent"]
                         ["observed_unique_market_conditions"],0)
        self.assertEqual(got["exclusion_reasons"]["blocked_original_quote_unavailable"],1)
        self.assertIsNone(got["by_cohort"]["blocked_recent"]
                          ["scenario_models"]["fee_5pct"]["model_hold_roi"])

    def test_quote_at_or_after_settlement_cannot_be_counted(self):
        got=m.build_report(checkpoint([archived(quoted=250)]),forward([]))
        self.assertEqual(got["exclusion_reasons"]["blocked_not_verifiably_before_settlement"],1)

    def test_same_market_in_two_groups_excluded_not_double_counted(self):
        got=m.build_report(checkpoint([archived()]),forward([sent(c=C)]))
        self.assertEqual(got["market_condition_duplicates_excluded"],2)
        self.assertEqual(got["market_condition_collision_inventory"]
                         ["conditions_crossing_rejected_and_sent"],1)
        self.assertEqual(got["market_condition_collision_inventory"]
                         ["quote_rows_in_cross_cohort_conditions"],2)
        self.assertEqual(got["by_cohort"]["telegram_sent"]
                         ["observed_unique_market_conditions"],0)
        self.assertEqual(got["by_cohort"]["blocked_recent"]
                         ["observed_unique_market_conditions"],0)

    def test_within_rejected_collision_is_not_mistaken_for_sent_overlap(self):
        a=archived(c=C)
        b=archived(c=C)
        b["token_id"]="different-token"
        got=m.build_report(checkpoint([a,b]),forward([sent(c=D)]))
        v=got["market_condition_collision_inventory"]
        self.assertEqual(v["conditions_crossing_rejected_and_sent"],0)
        self.assertEqual(v["conditions_repeated_only_in_rejected_group"],1)
        self.assertEqual(v["conditions_repeated_only_in_sent_group"],0)
        self.assertEqual(got["by_cohort"]["blocked_recent"]
                         ["observed_unique_market_conditions"],0)
        self.assertEqual(got["by_cohort"]["telegram_sent"]
                         ["observed_unique_market_conditions"],1)

    def test_prospective_quote_provenance_required(self):
        f=forward([sent()])
        f["forward_only_no_backfilled_quotes"]=False
        with self.assertRaises(ValueError):
            m.build_report(checkpoint([]),f)

    def test_unverified_and_unpriced_rows_not_assumed_losses(self):
        r=archived(result="unknown_resolution_pending")
        got=m.build_report(checkpoint([r]),forward([]))
        self.assertEqual(got["exclusion_reasons"]["blocked_outcome_not_final"],1)
        self.assertEqual(got["by_cohort"]["blocked_recent"]["losses"],0)
        self.assertIsNone(got["by_cohort"]["blocked_recent"]
                          ["scenario_models"]["fee_5pct"]["model_hold_roi"])

    def test_rejected_quote_must_precede_source_resolution(self):
        row=archived()
        row["original_book_quote"]["source_trade_at"]=112
        got=m.build_report(checkpoint([row]),forward([]))
        self.assertEqual(got["exclusion_reasons"]["blocked_not_verifiably_before_settlement"],1)

    def test_one_to_one_support_uses_price_and_time_not_any_lookalike(self):
        rows = [
            {"cohort":"telegram_sent", "market":C, "ask":.52, "stake":5,
             "quoted_at":1000, "result":"win", "source_trade_to_quote_seconds":None},
            {"cohort":"blocked_recent", "market":D, "ask":.54, "stake":5,
             "quoted_at":1100, "result":"loss", "source_trade_to_quote_seconds":20},
            {"cohort":"blocked_recent", "market":"0x"+"e"*64, "ask":.97, "stake":5,
             "quoted_at":1100, "result":"win", "source_trade_to_quote_seconds":20},
            {"cohort":"blocked_recent", "market":"0x"+"f"*64, "ask":.50, "stake":5,
             "quoted_at":100000, "result":"win", "source_trade_to_quote_seconds":20},
        ]
        support=m.quote_price_time_support(rows)
        self.assertEqual(support["one_to_one_pairs"],1)
        self.assertEqual(support["sent_unpaired"],0)
        self.assertEqual(support["blocked_unpaired"],2)
        self.assertEqual(support["max_pairing_quote_time_gap_seconds"],100)
        self.assertAlmostEqual(support["max_pairing_ask_gap"],.02)
        self.assertIsNone(support["conditional_odds_time_difference_in_roi"])
        self.assertIsNone(support["causal_filter_effect"])
        self.assertFalse(support["same_league_verified"])

    def test_one_rejected_condition_cannot_pair_with_two_sent_conditions(self):
        rows=[
            {"cohort":"telegram_sent", "market":C, "ask":.52, "stake":5,
             "quoted_at":1000, "result":"win", "source_trade_to_quote_seconds":None},
            {"cohort":"telegram_sent", "market":"0x"+"e"*64, "ask":.53,
             "stake":5, "quoted_at":1000, "result":"loss",
             "source_trade_to_quote_seconds":None},
            {"cohort":"blocked_recent", "market":D, "ask":.52, "stake":5,
             "quoted_at":990, "result":"win", "source_trade_to_quote_seconds":15},
        ]
        overlap=m.quote_price_time_support(rows)
        self.assertEqual(overlap["one_to_one_pairs"],1)
        self.assertEqual(overlap["sent_unpaired"],1)

    def test_price_time_support_empty_remains_unknown_not_zero_causal_effect(self):
        overlap=m.quote_price_time_support([])
        self.assertEqual(overlap["one_to_one_pairs"],0)
        self.assertIsNone(overlap["max_pairing_ask_gap"])
        self.assertIsNone(overlap["causal_filter_effect"])
        self.assertFalse(overlap["eligible_for_changing_live_gates"])

    def test_maturity_inventory_keeps_pending_quotes_without_declaring_them_losses(self):
        first=archived(c=C, result="unknown_resolution_pending",
                       quoted=1792250000)
        second=archived(c=D, result="win", quoted=1792250100)
        f=forward([])
        summary=m.observable_quote_outcome_maturity(checkpoint([first,second]),f)
        blocked=summary["cohorts"]["blocked_recent"]
        self.assertEqual(blocked["unique_quote_conditions"],2)
        self.assertEqual(blocked["final_outcomes_in_quote_journal"],1)
        self.assertEqual(blocked["not_final_or_unverified_in_quote_journal"],1)
        self.assertFalse(summary["complete_outcome_population_verified"])

    def test_maturity_inventory_reports_missing_original_proof_not_zero_losses(self):
        got=m.observable_quote_outcome_maturity(
            checkpoint([archived(original=False)]),forward([]))
        blocked=got["cohorts"]["blocked_recent"]
        self.assertEqual(blocked["unique_quote_conditions"],0)
        self.assertEqual(got["missing_original_quote_proof"]
                         ["blocked_missing_original_quote"],1)
        self.assertEqual(blocked["final_outcomes_in_quote_journal"],0)

    def test_maturity_inventory_deduplicates_same_market_before_quote_counts(self):
        a=archived()
        b=archived()
        b["token_id"]="different-token"
        got=m.observable_quote_outcome_maturity(
            checkpoint([a,b]),forward([]))
        block=got["cohorts"]["blocked_recent"]
        self.assertEqual(block["unique_quote_conditions"],0)
        self.assertEqual(block["duplicate_condition_rows_excluded"],2)


if __name__ == "__main__":
    unittest.main()
