"""No-network tests for public read-only wide-wallet pilot."""
import unittest
from unittest import mock
from tools import v33_broad_sports_capacity_canary as s
NOW=1792315200
def w(i):
    return "0x"+format(i+1,"040x")
def data(n=50):
    return {"schema":"v33_broad_sports_research_1",
            "research_queue":[{"wallet":w(i),"in_current_300_cursor_snapshot":i%2==0}
                              for i in range(n)]}

class WideProbeTests(unittest.TestCase):
    def test_sample_only_unmonitored_and_bounded(self):
        a=s.choose(data())
        self.assertEqual(len(a),24)
        self.assertEqual(a[0],w(1))
        self.assertEqual(a[1],w(3))
        with self.assertRaises(ValueError):
            s.choose(data(),count=100)

    def test_pagination_more_means_incomplete_window(self):
        row={"proxy_wallet":w(1),"side":"BUY"}
        call=mock.Mock(return_value={"data":[row],
                        "pagination":{"has_more":True,"next_cursor":"next"}})
        r=s.sample(w(1),NOW,api=call)
        self.assertEqual(call.call_count,1)
        self.assertTrue(r["has_more"])
        self.assertFalse(r["complete_one_hour_trade_window"])
        self.assertEqual(r["buy_rows_in_first_page"],1)
        self.assertEqual(call.call_args.args[1]["start"],NOW-3600)

    def test_timeout_and_invalid_response_do_not_become_empty_wallets(self):
        fail=s.sample(w(1),NOW,api=lambda *a,**k: (_ for _ in ()).throw(TimeoutError()))
        self.assertFalse(fail["ok"])
        self.assertFalse(fail["complete_one_hour_trade_window"])
        invalid=s.sample(w(2),NOW,api=lambda *a,**k: {"data":[]})
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"],"ValueError")

    def test_capacity_metrics_refuse_to_extrapolate_derived_roi(self):
        items=[
            {"ok":True,"latency_seconds":.5,"complete_one_hour_trade_window":True,
             "buy_rows_in_first_page":10},
            {"ok":True,"latency_seconds":2,"has_more":True,"complete_one_hour_trade_window":False,
             "buy_rows_in_first_page":200},
            {"ok":False,"latency_seconds":10,"error":"TimeoutError",
             "complete_one_hour_trade_window":False}]
        out=s.report_results(items)
        self.assertEqual(out["checked_wallets"],3)
        self.assertEqual(out["complete_one_hour_windows"],1)
        self.assertEqual(out["buy_rows_in_fully_retrieved_one_hour_windows"],10)
        self.assertEqual(out["errors"]["TimeoutError"],1)
        self.assertFalse(out["live_wallet_watchlist_updated"])
        self.assertEqual(out["telegram_sends"],0)

if __name__=="__main__":
    unittest.main()
