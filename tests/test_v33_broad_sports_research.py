"""Offline test of broad research pool; this must never edit live monitoring."""
import unittest
from tools import v33_broad_sports_research as b
NOW=1792315200
def wallet(i):
    return "0x"+format(i,"040x")

def input_data(n=1300):
    data={}
    for i in range(n):
        data[wallet(i+1)]={
            "sports_last_buy":NOW-(i%200)*120,
            "mid_buys_sampled":3,
            "buy_rows_sampled":10,
            "score_checked_at":NOW-100,
            "n":100,"winrate":.99,"recent_realized_pnl":1000,
            "profit_factor":9,
        }
    return ({"schema":"v33_wallet_universe_1","wallets":data},
            {"schema":"v33_wallet_shortlist_1",
             "shortlist":[{"wallet":wallet(i+1)} for i in range(350)]},
            {"version":33,"cursors":{wallet(i+1):NOW for i in range(300)}})

class BroadPoolTests(unittest.TestCase):
    def test_1200_is_a_research_only_queue_not_a_telegram_allowlist(self):
        result=b.build_report(*input_data(),now=NOW)
        self.assertEqual(result["research_queue_count"],1200)
        self.assertEqual(result["input_universe_wallets"],1300)
        self.assertEqual(result["wallets_live_approved"],0)
        self.assertFalse(result["safe_for_automatic_telegram_expansion"])
        self.assertFalse(result["not_a_profitability_ranking"] is False)
        self.assertFalse(any(r["live_approved"] for r in result["research_queue"]))
        self.assertEqual(result["cost_projection_wallet_polls_per_full_cycle"]
                         ["extra_over_current_if_1000"],700)

    def test_no_sports_trade_is_never_fabricated_as_active(self):
        universe, shortlist, state=input_data(15)
        for i,row in enumerate(universe["wallets"].values()):
            if i<10: row["sports_last_buy"]=None
        report=b.build_report(universe,shortlist,state,now=NOW)
        self.assertEqual(report["research_queue_count"],5)
        self.assertEqual(report["research_queue_unfilled_slots"],1195)
        self.assertTrue(all(x["last_sampled_sports_buy_at"] for x in report["research_queue"]))

    def test_recent_sports_activity_dominates_old_closed_snapshot_score(self):
        univ,short,st=input_data(2)
        low=univ["wallets"][wallet(1)]
        low.update({"sports_last_buy":NOW-20, "n":1,"winrate":0,
                    "recent_realized_pnl":-500,"profit_factor":.1})
        high=univ["wallets"][wallet(2)]
        high["sports_last_buy"]=NOW-600
        report=b.build_report(univ,short,st,now=NOW,limit=2)
        self.assertEqual(report["research_queue"][0]["wallet"],wallet(1))
        self.assertFalse(report["research_queue"][0]["risk_page_verified"])
        self.assertEqual(report["research_queue"][0]["legacy_closed_score_status"],
                         "LEGACY_CLOSED_SNAPSHOT_FAIL")

    def test_activity_older_than_seven_days_not_called_active(self):
        univ,short,st=input_data(2)
        univ["wallets"][wallet(1)]["sports_last_buy"]=NOW-8*86400
        univ["wallets"][wallet(2)]["sports_last_buy"]=NOW+9999
        result=b.build_report(univ,short,st,now=NOW)
        self.assertEqual(result["research_queue_count"],0)
        self.assertEqual(result["sampled_sports_buy_7d_total"],0)

    def test_history_missing_remains_unknown_not_profitable(self):
        univ,short,st=input_data(1)
        univ["wallets"][wallet(1)].pop("score_checked_at")
        result=b.build_report(univ,short,st,now=NOW)
        self.assertEqual(result["research_queue"][0]["legacy_closed_score_status"],
                         "UNVERIFIED_OR_EXPIRED")
        self.assertFalse(result["research_queue"][0]
                         ["independent_complete_wallet_economics"])

    def test_invalid_schema_and_capacity_fail_closed(self):
        uni,short,st=input_data(1)
        with self.assertRaises(ValueError):
            b.build_report({"schema":"something else"},short,st,now=NOW)
        with self.assertRaises(ValueError):
            b.build_report(uni,short,st,now=NOW,limit=1201)

    def test_real_monitor_comparison_is_distinct_from_shortlist(self):
        uni,short,st=input_data(400)
        result=b.build_report(uni,short,st,now=NOW)
        self.assertEqual(result["queue_overlap_current_runtime_cursor_snapshot"],300)
        self.assertEqual(result["queue_not_in_current_runtime_cursor_snapshot"],100)
        self.assertEqual(result["queue_overlap_350_shortlist"],350)

if __name__=="__main__":
    unittest.main()
