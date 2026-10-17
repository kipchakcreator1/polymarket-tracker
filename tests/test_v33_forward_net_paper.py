"""Book-quote only, fees in shares, zero unsupported realized profits."""
import unittest
from tools import v33_forward_net_paper as mod
W="0x"+"a"*40
C="0x"+"b"*64
T="012345"
K=W+"|"+C+"|"+T

def quote(decision=None,shares=10.,asof=1000):
    value={"wallet":W,"condition":C,"token_id":T,
           "topic":"sports","hypothetical_copy_ask":.5,
           "hypothetical_copy_stake_usd":5,
           "hypothetical_copy_shares":shares,
           "book_quoted_at":asof,"source_trade_at":999}
    if decision:
        value["decision"]=decision
    return value

def truth(direction="win",settle=2000):
    return {"updated_at":2001,"alerts":{"rows":[{
        "wallet":W,"condition":C,"token_id":T,
        "result":direction,"settled_at":settle}]},
        "shadows":{"rows":[]}}

class TestForwardNetBook(unittest.TestCase):
    def test_share_deducted_taker_fee_winner_and_loser(self):
        self.assertAlmostEqual(mod.paper_pnl(5,.5,True,0),5)
        # 10 shares, 0.05*(1-.5)=.025 share fee / share => 9.75 payout
        self.assertAlmostEqual(mod.paper_pnl(5,.5,True,.05),4.75)
        # BUY fee paid in shares; loss payout still zero.
        self.assertAlmostEqual(mod.paper_pnl(5,.5,False,.05),-5)
        self.assertLess(mod.paper_pnl(5,.5,True,.05,1.05),
                        mod.paper_pnl(5,.5,True,.05,1))

    def test_missing_quote_share_volume_prevents_fake_net_result(self):
        q=quote(shares=None)
        result=mod.evaluate({"signal_evidence":{K:q}},truth())
        self.assertEqual(result["quote_coverage"]["missing_book_share_depth_evidence"],1)
        self.assertIsNone(result["by_stream"]["telegram_sent"]["fee_0.05_model_roi"])
        self.assertIsNone(result["by_stream"]["telegram_sent"]["actual_net_copy_roi"])

    def test_no_prospective_quotes_not_zero_roi(self):
        result=mod.evaluate({"sent":{K:1000}},truth())
        self.assertIsNone(result["by_stream"]["telegram_sent"]["fee_0.05_model_roi"])
        self.assertIsNone(result["by_stream"]["shadow_only"]["fee_0.05_model_roi"])
        self.assertEqual(result["by_stream"]["telegram_sent"]["qualifying_resolved_observations"],0)
        self.assertEqual(result["positions"],[])

    def test_de_dup_shadow_and_sent_record_for_same_bet(self):
        r=mod.evaluate({"shadow_evidence":{K:quote(decision="sent_telegram")},
                        "signal_evidence":{K:quote()}},truth())
        self.assertEqual(len(r["positions"]),1)
        self.assertEqual(r["positions"][0]["stream"],"telegram_sent")
        self.assertEqual(r["by_stream"]["telegram_sent"]["qualifying_resolved_observations"],1)
        self.assertEqual(r["by_stream"]["telegram_sent"]["fee_0.05_model_hold_pnl_usd"],4.75)
        self.assertEqual(r["by_stream"]["telegram_sent"]["actual_filled_orders"],0)

    def test_unsent_shadow_remains_separate(self):
        r=mod.evaluate({"shadow_evidence":{K:quote("blocked_recent")}},truth())
        self.assertEqual(r["by_stream"]["shadow_only"]["qualifying_resolved_observations"],1)
        self.assertEqual(r["by_stream"]["telegram_sent"]["qualifying_resolved_observations"],0)
        self.assertEqual(r["positions"][0]["decision"],"blocked_recent")

    def test_pending_and_fee_absent_are_not_marked_profitable(self):
        r=mod.evaluate({"signal_evidence":{K:quote()}},truth("unknown_resolution_pending",None))
        self.assertEqual(r["quote_coverage"]["awaiting_independent_official_settlement"],1)
        self.assertNotIn("hypothetical_pnl_usd",r["positions"][0])
        self.assertIsNone(r["by_stream"]["telegram_sent"]["fee_0.05_model_roi"])

    def test_delayed_quote_at_or_after_game_does_not_count(self):
        r=mod.evaluate({"signal_evidence":{K:quote(asof=3000)}},truth(settle=2000))
        self.assertEqual(r["quote_coverage"]["entry_not_before_verified_settlement"],1)

    def test_inconsistent_book_depth_is_rejected(self):
        r=mod.evaluate({"signal_evidence":{K:quote(shares=1)}},truth())
        self.assertEqual(r["quote_coverage"]["inconsistent_book_shares_vwap"],1)

    def test_invalid_inputs_fail_closed(self):
        self.assertIsNone(mod.paper_pnl(5,1,True))
        self.assertIsNone(mod.paper_pnl(5,.5,"win"))
        self.assertIsNone(mod.paper_pnl(5,.5,True,fee_rate=.3))
        self.assertIsNone(mod.paper_pnl(5,.99,True,entry_price_multiplier=1.05))

if __name__=="__main__":
    unittest.main()
