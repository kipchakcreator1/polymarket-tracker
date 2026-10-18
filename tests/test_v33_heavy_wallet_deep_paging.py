"""No-network deep-page followup regressions."""
import unittest
from unittest import mock
from tools import v33_heavy_wallet_deep_paging as d

NOW = 1792318500

def wallet(i):
    return "0x"+format(i+1,"040x")


def sources(n=4):
    wallets=[wallet(i+1) for i in range(n)]
    broad={"schema":"v33_broad_sports_research_1",
           "research_queue":[{"wallet":w,"in_current_300_cursor_snapshot":False}
                             for w in wallets]}
    initial={"schema":"v33_60_wallet_paginated_capacity_1",
             "captured_at":NOW,
             "research_sample_details":[{
                 "wallet_prefix":w[:14],"lookback_seconds":21600,
                 "stage":"sampled_busy_6h",
                 "pagination_state":"UNKNOWN_PAGE_LIMIT",
                 "pages_requested":5
             } for w in wallets]}
    return initial,broad


class DeepPagingTests(unittest.TestCase):
    def test_only_first_pass_capped_and_unique_unmonitored_resolved(self):
        first,broad=sources(3)
        first["research_sample_details"].append({
            "wallet_prefix":wallet(99)[:14],"lookback_seconds":21600,
            "pagination_state":"COMPLETE_QUERY_WINDOW","stage":"recent_1h"})
        out,unknown,overflow=d.choose_missing(first,broad)
        self.assertEqual(len(out),3)
        self.assertEqual(unknown,[])
        self.assertEqual(overflow,0)

    def test_deep_page_exhaustion_does_not_invent_complete_history(self):
        first,broad=sources(1)
        request_counter=[]
        def fake(path,params,**kwargs):
            request_counter.append(params.get("cursor"))
            page=len(request_counter)
            return {"data":[],"pagination":{
                "has_more":page<=6,
                "next_cursor":f"page_{page}" if page<=6 else None}}
        result=d.run(first,broad,api=fake)
        self.assertEqual(result["complete_query_windows_after_12_page_cap"],1)
        self.assertEqual(result["deep_total_actual_v2_pages_including_replayed_first_5"],7)
        self.assertEqual(result["started_deep_wallets"],1)
        self.assertFalse(result["automatically_promote_wallets"])
        self.assertEqual(request_counter[0],None)
        self.assertEqual(request_counter[1],"page_1")

    def test_12_page_cap_still_unknown_if_thirteen_required(self):
        first,broad=sources(1)
        cursor_count=0
        def fake(path,params,**kwargs):
            nonlocal cursor_count
            cursor_count+=1
            return {"data":[],"pagination":{"has_more":True,
                                             "next_cursor":f"p{cursor_count}"}}
        result=d.run(first,broad,api=fake)
        self.assertEqual(cursor_count,12)
        self.assertEqual(result["still_incomplete_or_error"],1)
        self.assertEqual(result["complete_query_windows_after_12_page_cap"],0)

    def test_ambiguous_prefix_cannot_resolve_to_wrong_wallet(self):
        first,broad=sources(1)
        original=broad["research_queue"][0]["wallet"]
        collision=original[:-1]+"f"
        broad["research_queue"].append({
            "wallet":collision,"in_current_300_cursor_snapshot":False})
        selected,unknown,_=d.choose_missing(first,broad)
        self.assertEqual(selected,[])
        self.assertEqual(len(unknown),1)

    def test_rate_limit_is_never_interpreted_as_clean_and_aborts_batches(self):
        first,broad=sources(4)
        def limited(*args,**kwargs):
            raise RuntimeError("429 Too Many Requests")
        out=d.run(first,broad,api=limited)
        self.assertEqual(out["started_deep_wallets"],2)
        self.assertTrue(out["stopped_after_rate_limit"])
        self.assertEqual(out["complete_query_windows_after_12_page_cap"],0)
        self.assertEqual(out["status_breakdown"]["UNKNOWN_API_OR_SCHEMA_ERROR"],2)

    def test_corrupt_benchmark_rejected(self):
        initial,broad=sources(1)
        initial["schema"]="invalid"
        with self.assertRaises(ValueError):
            d.choose_missing(initial,broad)
        initial["schema"]="v33_60_wallet_paginated_capacity_1"
        initial["captured_at"]=None
        with self.assertRaises(ValueError):
            d.run(initial,broad)

if __name__=="__main__":
    unittest.main()
