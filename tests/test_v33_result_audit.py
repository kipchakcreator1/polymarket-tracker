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
