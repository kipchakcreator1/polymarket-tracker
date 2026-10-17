"""Offline tests: no real API requests, orders or Telegram."""
import unittest
from unittest import mock

from tools import v33_result_audit as a

W = "0x" + "a"*40
C = "0x" + "1"*64
G = {"conditionId": C, "category": "Sports",
     "clobTokenIds": '["yes","no"]', "outcomes": '["Yes","No"]'}
R = {"condition_id": C, "status": "resolved",
     "resolved_at": "2026-10-15T00:00:00Z", "payouts": [0, 1000000]}


def fill(token="yes", side="BUY", price=.4, timestamp=1791950000):
    return {"condition_id": C, "token_id": token, "proxy_wallet": W,
            "side": side, "price": price, "size": 100, "timestamp": timestamp}


class TruthTests(unittest.TestCase):
    def test_proven_sports_loss_even_after_profitable_cashout(self):
        rows = [fill(), fill(side="SELL", price=.65)]
        selected = a.candidate_positions(rows)
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0][3])
        self.assertEqual(a.verdict(G, R, selected[0][1]), "loss")

    def test_real_win_and_loss_follow_final_payout(self):
        self.assertEqual(a.verdict(G, R, "no"), "win")
        self.assertEqual(a.verdict(G, R, "yes"), "loss")

    def test_disputed_unsettled_unknown(self):
        self.assertEqual(a.verdict(G, dict(R, status="proposed"), "yes"), "unknown")
        self.assertEqual(a.verdict(G, dict(R, resolved_at=None), "yes"), "unknown")

    def test_push_is_not_inflated(self):
        self.assertEqual(a.verdict(G, dict(R, payouts=[500000,500000]), "yes"),
                         "push_or_partial")

    def test_token_mismatch_fails_closed(self):
        self.assertEqual(a.verdict(G, R, "unknown-token"), "unknown")

    def test_only_official_sports_count(self):
        self.assertEqual(a.verdict(dict(G, category="Entertainment"), R, "no"),
                         "not_verified_sport")

    def test_both_sides_are_not_independent_predictions(self):
        self.assertEqual(a.candidate_positions([fill(),fill(token="no")]), [])
        self.assertEqual(len(a.candidate_positions([fill(),fill(price=.55)])), 1)

    def test_partial_trade_history_not_certified(self):
        with mock.patch.object(a, "fetch_wallet_trades", return_value=([fill()], False)), \
             mock.patch.object(a, "classify", return_value=("loss",1792000000)):
            sample = a.sample_wallet(W, {}, {})
        self.assertFalse(sample["trade_history_complete"])
        self.assertFalse(sample["certified_sample"])
        self.assertEqual(sample["losses"], 1)

    def test_fewer_than_twenty_observations_not_certified(self):
        with mock.patch.object(a, "fetch_wallet_trades", return_value=([fill()], True)), \
             mock.patch.object(a, "classify", return_value=("win",1792000000)):
            sample = a.sample_wallet(W, {}, {})
        self.assertFalse(sample["certified_sample"])
        self.assertEqual(sample["wins"], 1)

    def test_cursor_success_and_unsafe_reuse(self):
        pages=[{"data":[fill()], "pagination":{"has_more":True,"next_cursor":"x"}},
               {"data":[], "pagination":{"has_more":False,"next_cursor":None}}]
        with mock.patch.object(a.api,"request_json",side_effect=pages) as req:
            items, complete=a.fetch_wallet_trades(W)
        self.assertTrue(complete)
        self.assertEqual(len(items),1)
        self.assertEqual(req.call_args_list[-1].args[1]["cursor"],"x")
        with mock.patch.object(a.api,"request_json",return_value=pages[0]):
            with self.assertRaises(ValueError):
                a.fetch_wallet_trades(W)

    def test_trade_history_asks_for_both_maker_and_taker_in_time_window(self):
        now=1792200000
        earlier=1792000000
        with mock.patch.object(a.api, "request_json",
             return_value={"data":[fill(timestamp=earlier)],
                           "pagination":{"has_more":False}}) as req:
            rows, complete=a.fetch_wallet_trades(W, now)
        self.assertTrue(complete)
        self.assertEqual(len(rows),1)
        params=req.call_args.args[1]
        self.assertEqual(params["taker_only"], "false")
        self.assertEqual(params["start"], now-a.TRADE_LOOKBACK_DAYS*86400)
        self.assertEqual(params["end"],now)

    def test_time_window_violation_is_not_called_complete(self):
        now=1792200000
        old=fill(timestamp=now-a.TRADE_LOOKBACK_DAYS*86400-1)
        with mock.patch.object(a.api,"request_json",
             return_value={"data":[old],"pagination":{"has_more":False}}):
            with self.assertRaisesRegex(ValueError,"ignored requested time window"):
                a.fetch_wallet_trades(W,now)

    def test_first_buy_is_not_earlier_sell_activity(self):
        rows=[fill(side="SELL",timestamp=1791900000),
              fill(side="BUY",timestamp=1792000000)]
        selected=a.candidate_positions(rows)
        self.assertEqual(selected[0][2],1792000000)

    def test_position_truth_requires_trade_buy_provenance_to_certify(self):
        closed={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
                "status":"CLOSED","first_entry_at":1791900000,
                "realized_pnl":50}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([closed],True)), \
             mock.patch.object(a,"classify",
                               return_value=("loss",1792200000)):
            no_trade=a.position_truth_sample(
                W,{}, {}, {"rows":[],"complete":True})
            matched=a.position_truth_sample(
                W,{}, {}, {"rows":[fill(timestamp=1792000000)],"complete":True})
        self.assertEqual(no_trade["resolved_sports_sample"],0)
        self.assertEqual(no_trade["status_counts"]["trade_entry_unverified"],1)
        self.assertEqual(matched["losses"],1)
        self.assertFalse(matched["certified_sample"])

    def test_redeemable_positions_include_archived_losses(self):
        empty={"data":[],"pagination":{"has_more":False}}
        with mock.patch.object(a.api,"request_json",
                               side_effect=[empty,empty]) as req:
            a.fetch_position_history(W)
        self.assertEqual(req.call_args_list[1].args[1]["include_archived"],"true")

    def test_closed_profitable_position_can_be_wrong_sports_prediction(self):
        closed={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
                "status":"CLOSED","first_entry_at":1791900000,
                "realized_pnl":25}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([closed],True)), \
             mock.patch.object(a,"classify",
                               return_value=("loss",1792000000)):
            sample=a.position_truth_sample(W,{}, {})
        self.assertEqual(sample["losses"],1)
        self.assertEqual(sample["profitable_closed_wrong_direction"],1)
        self.assertFalse(sample["certified_sample"])

    def test_unredeemed_losing_sports_position_is_not_ignored(self):
        loser={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
               "status":"REDEEMABLE","first_entry_at":1791900000,
               "realized_pnl":0}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([loser],True)), \
             mock.patch.object(a,"classify",
                               return_value=("loss",1792000000)):
            sample=a.position_truth_sample(W,{}, {})
        self.assertEqual(sample["losses"],1)

    def test_closed_and_redeemable_are_both_queried(self):
        valid=lambda status: {"data":[],"pagination":{
            "has_more":False,"next_cursor":None}}
        with mock.patch.object(a.api,"request_json",
                               side_effect=[valid("CLOSED"),valid("REDEEMABLE")]) as req:
            rows,complete=a.fetch_position_history(W)
        self.assertTrue(complete)
        self.assertEqual(rows,[])
        self.assertEqual([x.args[1]["status"] for x in req.call_args_list],
                         ["CLOSED","REDEEMABLE"])

    def test_partial_position_pages_cannot_certify_quality(self):
        closed={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
                "status":"CLOSED","first_entry_at":1791900000}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([closed],False)), \
             mock.patch.object(a,"classify",
                               return_value=("win",1792000000)):
            value=a.position_truth_sample(W,{}, {})
        self.assertFalse(value["certified_sample"])
        self.assertFalse(value["position_history_complete"])

    def test_profitable_closed_rate_separate_from_unredeemed_loss_cluster(self):
        rows=[{"condition_id":"0x"+str(i+1).zfill(64),
               "token_id":str(i+1),"status":"CLOSED","realized_pnl":100}
              for i in range(20)]
        rows += [{"condition_id":"0x"+str(i+100).zfill(64),
                  "token_id":str(i+100),"status":"REDEEMABLE",
                  "redeemable":True,"realized_pnl":0,
                  "unrealized_pnl":-600} for i in range(12)]
        result=a.position_accounting_snapshot(rows,True)
        self.assertEqual(result["profitable_closed_fraction"],1.0)
        self.assertEqual(result["unredeemed_major_losers_100usd"],12)
        self.assertEqual(result["unredeemed_loss_usd_observed"],-7200)
        self.assertTrue(result["not_sports_winrate"])

    def test_position_accounting_deduplicates_same_market_token(self):
        x={"condition_id":C,"token_id":"yes","status":"REDEEMABLE",
           "redeemable":True,"realized_pnl":0,"unrealized_pnl":-600}
        counts=a.position_accounting_snapshot([x,x],False)
        self.assertEqual(counts["unredeemed_losers"],1)
        self.assertFalse(counts["positions_complete"])

    def test_forward_paper_hold_requires_recorded_ask(self):
        key=W+"|"+C+"|yes"
        prior=[{"wallet":W,"condition":C,"token_id":"yes",
                "alert_at":1234,"result":"win","settled_at":2000}]
        proof={key:{"topic":"sports","hypothetical_copy_ask":.5,
                    "hypothetical_copy_stake_usd":5}}
        with mock.patch.object(a,"classify") as remote:
            rows=a.audit_signals({key:1234},{},{},prior,proof)
        remote.assert_not_called()
        self.assertAlmostEqual(rows[0]["paper_hold_pnl_no_fees"],5.0)
        no_proof=a.audit_signals({key:1234},{},{},prior,{})
        self.assertNotIn("paper_hold_pnl_no_fees",no_proof[0])

    def test_prior_resolved_alert_does_not_requery(self):
        key=W+"|"+C+"|yes"
        previous=[{"wallet":W,"condition":C,"token_id":"yes",
                   "alert_at":1234,"result":"loss","settled_at":2000}]
        with mock.patch.object(a,"classify") as remote:
            result=a.audit_signals({key:1234},{},{},previous)
        remote.assert_not_called()
        self.assertEqual(result[0]["result"],"loss")


if __name__=="__main__":
    unittest.main()
