"""Prove sports-copy price screening has no approval pathway and no missing win inflation."""
import unittest
from unittest import mock
from tools import v33_wallet_copy_cohort as m

A=m.FOCUS
B=m.NEGATIVE_CONTROL
C="0x"+"c"*40

def evidence(n=10, full=True):
    events=[{"token_id":str(i),"first_entry_at":1000+i,
             "resolved_at":3000+i,
             "source_first_buy_price":.6,
             "event_slug":f"ufc-fight-{i}","league":"UFC",
             "condition":"0x"+format(i,"064x"),"direction":"win"}
            for i in range(n)]
    return {"trade_window_complete":full,
            "market_scan_complete":full,
            "certified_sample":full,
            "position_truth":{"events":events,
                "certified_sample":full,
                "position_history_complete":full,
                "position_market_scan_complete":full,
                "position_accounting":{"unredeemed_major_losers_100usd":0}},
            "unverified_final_or_mapping_count":0}

def result(wallet=A, prices=(.8,.6), statuses=None):
    es=[]
    for i,p in enumerate(prices):
        status=statuses[i] if statuses is not None else "price_proxy_only"
        lag={str(l):({"status":status,
                    "roi_on_proxy_no_fees":p,
                    "roi_if_5pct_higher_entry_no_fees":p-.05}
               if status=="price_proxy_only" else {"status":status})
               for l in (0,60,300,600)}
        es.append({"league":"UFC","lags_seconds":lag})
    return {"wallet":wallet,"events":es,
            "history_api_errors":{},
            "common_cohort_mean_roi_at_each_lag_no_fees":{
                str(x):.5 for x in (0,60,300,600)}}

class CopyCohort(unittest.TestCase):
    def test_choose_includes_ufc_and_negative_control_before_optimistic_wallets(self):
        source={"wallets":{A:evidence(),B:evidence(),
                C:evidence(20)}}
        chosen,eligible=m.choose_wallets(source,max_wallets=2)
        self.assertEqual(chosen,[A,B])
        self.assertEqual(eligible,3)

    def test_even_great_historical_price_proxy_never_approves_live(self):
        with mock.patch.object(m,"MIN_EVENTS",2):
            data=evidence()
            r=m.screen_one(result(),data)
        self.assertEqual(r["status"],"OBSERVED_POSITIVE_PRICE_PROXY")
        self.assertFalse(r["eligible_for_live_buy"])
        self.assertFalse(r["copy_roi_verified"])
        self.assertAlmostEqual(r["roi_if_5pct_worse_entry_5m_no_fees"],.65)

    def test_missing_prices_and_unresolved_markets_get_downside_scenario(self):
        data=evidence()
        data["unverified_final_or_mapping_count"]=2
        with mock.patch.object(m,"MIN_EVENTS",1):
            r=m.screen_one(result(prices=(.5,.8),
               statuses=("price_proxy_only","no_fresh_preresolution_price")),
               data)
        self.assertEqual(r["common_cohort_all_four_lags"],1)
        self.assertAlmostEqual(r["common_price_coverage"],.5)
        self.assertEqual(r["status"],"INSUFFICIENT_PRICE_HISTORY")
        self.assertAlmostEqual(
            r["stress_if_all_missing_quotes_and_unverified_markets_lose"],
            (.45-3)/4)
        self.assertIn("unverified_final_outcomes",r["historical_data_warnings"])
        self.assertIn("missing_price_coverage",r["historical_data_warnings"])

    def test_complete_history_flags_required_to_remove_warning(self):
        data=evidence(full=False)
        with mock.patch.object(m,"MIN_EVENTS",2):
            r=m.screen_one(result(),data)
        self.assertIn("incomplete_trade_window",r["historical_data_warnings"])
        self.assertIn("incomplete_position_history",r["historical_data_warnings"])
        self.assertFalse(r["eligible_for_live_buy"])

    def test_source_report_never_contains_live_approval_even_with_winner(self):
        data={"updated_at":1000,"wallets":{A:evidence(),B:evidence()}}
        out=m.compile_report(data,{A:result()},[A,B],2)
        self.assertEqual(out["live_approved"],0)
        self.assertEqual(out["status_counts"]["FETCH_ERROR"],1)
        self.assertFalse(any(x["eligible_for_live_buy"] for x in
                             out["wallets"] if "eligible_for_live_buy" in x))

    def test_scope_dedupes_sports_events_before_proxies(self):
        d=evidence(12)
        d["position_truth"]["events"][0]["event_slug"]=\
            d["position_truth"]["events"][1]["event_slug"]
        events,amb=m.qualified_event_list(d)
        self.assertEqual(len(events),10)
        self.assertEqual(amb["multiple_markets_per_event"],1)

if __name__=="__main__":
    unittest.main()
