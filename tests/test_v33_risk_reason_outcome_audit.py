"""No-network audit: exact risk veto + official outcomes + original quotes."""
import unittest

from tools import v33_risk_reason_outcome_audit as m
from tools.v33_shadow_checkpoint import SCHEMA

W="0x"+"a"*40
OTHER="0x"+"c"*40
C="0x"+"b"*64
D="0x"+"d"*64
E="0x"+"e"*64


def row(condition=C, wallet=W, decision="blocked_recent",
        reason="major_unredeemed_loss_cluster_90d", result="loss",
        proof=True, checked=True, stamp=150):
    risk_gate={"blocked_recent":"recent",
               "blocked_active":"active_portfolio",
               "sent_telegram":"passed"}[decision]
    out={
        "wallet":wallet,"condition":condition,"token_id":"123",
        "alert_at":stamp,"result":result,"settled_at":500,
        "shadow_decision":decision,
    }
    if proof:
        out["original_book_quote"]={
            "topic":"sports","no_fill_assumed":True,
            "book_quoted_at":stamp,"source_trade_at":stamp-20,
            "hypothetical_copy_ask":.5,
            "hypothetical_copy_stake_usd":5,
            "hypothetical_copy_shares":10,
        }
    if checked:
        out.update({
            "risk_gate":risk_gate,
            "risk_reason":None if decision=="sent_telegram" else reason,
            "risk_checked_at":stamp+1,
            "risk_provenance":m.PROVENANCE,
        })
    return out


def data(rows):
    return {"schema":SCHEMA,"last_saved_at":900,"rows":rows,
            "meta":{"completed_runs":20}}


class ReasonOutcomeAuditTests(unittest.TestCase):
    def test_real_verified_reason_group_priced_hold_not_actual_fills(self):
        r=m.compile_report(data([row(result="win")]))
        group=r["by_proven_reason_or_legacy_group"][
            "recent:major_unredeemed_loss_cluster_90d"]
        self.assertEqual(group["observed_position_keys"],1)
        self.assertEqual(group["final_classifications"],1)
        self.assertEqual(group["position_level_final_wins"],1)
        self.assertEqual(group["unique_market_conditions_with_verifiable_original_quote"],1)
        self.assertAlmostEqual(group["hypothetical_models_for_final_quotes_only"][
            "assumed_5pct_fee"]["hypothetical_hold_roi"],.95)
        self.assertIsNone(group["actual_copy_roi"])
        self.assertIsNone(r["causal_benefit_from_changing_any_risk_gate"])
        self.assertFalse(r["allow_live_wallet_promotion_or_threshold_relaxation"])

    def test_unknown_legacy_reason_kept_separate_from_actual_major_loss(self):
        legacy=row(condition=D,checked=False,result="win")
        confirmed=row(condition=C,result="loss")
        report=m.compile_report(data([legacy,confirmed]))
        self.assertEqual(report["overall_legacy_or_unproven_reason_observations"],1)
        old=report["by_proven_reason_or_legacy_group"]["legacy_or_unproven_reason"]
        self.assertEqual(old["final_classifications"],1)
        self.assertEqual(old["position_level_final_wins"],1)
        self.assertEqual(report["by_proven_reason_or_legacy_group"][
            "recent:major_unredeemed_loss_cluster_90d"]["position_level_final_losses"],1)

    def test_pending_quote_not_counted_as_loss_or_model_return(self):
        r=m.compile_report(data([row(result="unknown_resolution_pending")]))
        group=r["by_proven_reason_or_legacy_group"][
            "recent:major_unredeemed_loss_cluster_90d"]
        self.assertEqual(group["unresolved_or_unverified"],1)
        self.assertEqual(group["final_classifications"],0)
        self.assertIsNone(group["hypothetical_models_for_final_quotes_only"][
            "assumed_5pct_fee"]["hypothetical_hold_roi"])

    def test_duplicate_market_across_reasons_cannot_double_count_return(self):
        a=row(condition=C,decision="blocked_recent",result="win")
        b=row(condition=C,decision="blocked_active",
              reason="active_portfolio_drawdown",wallet=OTHER,result="loss")
        r=m.compile_report(data([a,b]))
        self.assertEqual(r["colliding_conditions_excluded_from_all_model_groups"],1)
        for group in r["by_proven_reason_or_legacy_group"].values():
            self.assertEqual(group["unique_market_conditions_with_verifiable_original_quote"],0)
            self.assertEqual(group["final_positions_excluded_from_model"]
                             ["repeated_market_condition_across_all_cohorts"],1)

    def test_true_telegram_passed_is_not_mislabeled_as_rejected_risk(self):
        good=row(decision="sent_telegram",result="win")
        bad=row(condition=D,decision="sent_telegram",result="loss")
        bad["risk_reason"]="major_unredeemed_loss_cluster_90d"
        r=m.compile_report(data([good,bad]))
        self.assertEqual(r["by_proven_reason_or_legacy_group"][
            "passed:telegram_sent"]["observed_position_keys"],1)
        self.assertEqual(r["overall_legacy_or_unproven_reason_observations"],1)

    def test_price_after_settlement_and_missing_quote_remain_unpriceable(self):
        late=row(condition=C,stamp=600)
        no_quote=row(condition=D,proof=False)
        r=m.compile_report(data([late,no_quote]))
        group=r["by_proven_reason_or_legacy_group"][
            "recent:major_unredeemed_loss_cluster_90d"]
        self.assertEqual(group["final_classifications"],2)
        self.assertEqual(group["unique_market_conditions_with_verifiable_original_quote"],0)
        self.assertEqual(group["final_positions_excluded_from_model"][
            "missing_proven_before_settlement_chronology"],1)
        self.assertEqual(group["final_positions_excluded_from_model"][
            "missing_original_quote"],1)

    def test_malformed_source_rejected_before_market_aggregation(self):
        with self.assertRaises(ValueError):
            m.compile_report({"schema":"wrong","rows":[]})
        valid=row()
        invalid=dict(valid,wallet="invalid")
        r=m.compile_report(data([valid,invalid]))
        self.assertEqual(r["invalid_identity_rows_excluded"],1)
        self.assertEqual(r["unique_conditions_across_all_reason_groups"],1)


if __name__=="__main__":
    unittest.main()
