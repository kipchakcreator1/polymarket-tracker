import unittest
from tools import v33_recent_buy_audit as r

W="0x"+"a"*40
OLD="0x"+"1"*64
RECENT="0x"+"2"*64


def trade(condition,timestamp):
    return {"proxy_wallet":W,"condition_id":condition,"token_id":"yes",
            "side":"BUY","price":.5,"size":100,"timestamp":timestamp}


class FirstBuyCohort(unittest.TestCase):
    def test_latest_buy_scan_sees_unredeemed_loss_even_if_closed_first(self):
        closed={"condition_id":OLD,"token_id":"yes",
                "status":"CLOSED","realized_pnl":100}
        lost={"condition_id":RECENT,"token_id":"yes",
              "status":"REDEEMABLE","realized_pnl":0,
              "unrealized_pnl":-600}
        def classify(condition,token,gamma,res):
            return ("loss",3000) if condition==RECENT else ("win",3000)
        result=r.scan(W,[trade(OLD,1000),trade(RECENT,2000)],
                 [closed,lost],True,True,classifier=classify,cap=1)
        self.assertEqual(result["newest_BUYs_examined"],1)
        self.assertEqual(result["observed_verified_sports_losses"],1)
        self.assertEqual(result["observed_verified_sports_wins"],0)
        self.assertEqual(result["still_held_negative_positions_in_sample"],1)
        self.assertEqual(result["closed_or_redeemable_status_scanned"],
                         {"REDEEMABLE":1})
        self.assertFalse(result["true_sports_winrate_certified"])

    def test_missing_position_is_unknown_not_win_or_loss(self):
        result=r.scan(W,[trade(OLD,1000)],[],
                      True,False,classifier=lambda *a:("win",3000))
        self.assertEqual(result["outcomes"]["unknown_position_absent"],1)
        self.assertEqual(result["observed_verified_sports_wins"],0)

    def test_source_buy_after_market_settlement_is_not_prediction(self):
        pos={"condition_id":OLD,"token_id":"yes",
             "status":"CLOSED","realized_pnl":5}
        result=r.scan(W,[trade(OLD,3000)],[pos],True,True,
                      classifier=lambda *a:("win",2999))
        self.assertEqual(result["outcomes"]["unknown_buy_after_settlement"],1)
        self.assertEqual(result["observed_verified_sports_wins"],0)

if __name__=="__main__":
    unittest.main()
