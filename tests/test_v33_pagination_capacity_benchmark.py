"""Zero-network tests for staged wallet pagination capacity and safe abort."""
import unittest
from unittest import mock

from tools import v33_pagination_capacity_benchmark as c

NOW = 1792318500


def wallet(i):
    return "0x" + format(i+1,"040x")


def report(count=85):
    return {"schema":"v33_broad_sports_research_1",
            "research_queue":[{
                "wallet":wallet(i),
                "in_current_300_cursor_snapshot":i < 8,
                "all_buy_rows_sampled_nonunique":i*10,
                "mid_price_buy_rows_sampled_nonunique":i
            } for i in range(count)]}


def clock():
    return 1.0


class PaginationCapacityTests(unittest.TestCase):
    def test_stage_selection_is_bounded_distinct_and_excludes_live(self):
        rows=c.select_wallets(report())
        self.assertEqual(len(rows),60)
        self.assertEqual(sum(stage=="recent_1h" for _,_,stage in rows),48)
        self.assertEqual(sum(stage=="sampled_busy_6h" for _,_,stage in rows),12)
        self.assertEqual(len({w for w,_,_ in rows}),60)
        self.assertEqual(rows[0][0],wallet(8))
        self.assertTrue(all(w not in {wallet(i) for i in range(8)}
                            for w,_,_ in rows))
        self.assertEqual(rows[48][0],wallet(84))
        self.assertEqual(rows[48][1],21600)

    def test_full_two_page_cursor_history_requires_terminal_page(self):
        received=[]
        def fetch(path,params,**kwargs):
            received.append(dict(params))
            if "cursor" not in params:
                return {"data":[{"proxy_wallet":wallet(8),"side":"BUY"}],
                        "pagination":{"has_more":True,"next_cursor":"token2"}}
            return {"data":[{"proxy_wallet":wallet(8),"side":"SELL"},
                            {"proxy_wallet":wallet(9),"side":"BUY"}],
                    "pagination":{"has_more":False,"next_cursor":None}}
        row=c.check(wallet(8),3600,"recent_1h",NOW,api=fetch,clock=clock)
        self.assertEqual(row["pagination_state"],"COMPLETE_QUERY_WINDOW")
        self.assertEqual(row["pages_requested"],2)
        self.assertEqual(row["reported_buy_rows_nonunique"],1)
        self.assertEqual(row["unexpected_other_wallet_rows"],1)
        self.assertEqual(received[1]["cursor"],"token2")
        self.assertNotIn("cursor",received[0])
        self.assertFalse(row["complete_history_beyond_query_window"])

    def test_cursor_cycle_never_counts_as_complete(self):
        def fetch(path,params,**kwargs):
            return {"data":[],"pagination":{"has_more":True,"next_cursor":"again"}}
        row=c.check(wallet(8),3600,"recent_1h",NOW,api=fetch,clock=clock)
        self.assertEqual(row["pagination_state"],"UNKNOWN_BAD_CURSOR")
        self.assertEqual(row["pages_requested"],2)

    def test_cap_at_five_pages_is_not_renamed_complete(self):
        count=0
        def fetch(path,params,**kwargs):
            nonlocal count
            count+=1
            return {"data":[],"pagination":{"has_more":True,
                                              "next_cursor":str(count)}}
        row=c.check(wallet(8),21600,"sampled_busy_6h",NOW,
                    api=fetch,clock=clock)
        self.assertEqual(count,c.MAX_PAGES)
        self.assertEqual(row["pagination_state"],"UNKNOWN_PAGE_LIMIT")
        self.assertEqual(row["pages_requested"],c.MAX_PAGES)

    def test_api_429_is_unknown_and_explicitly_rate_limited(self):
        def denied(*args,**kwargs):
            raise RuntimeError("HTTP 429 Too Many Requests")
        row=c.check(wallet(8),3600,"recent_1h",NOW,api=denied,clock=clock)
        self.assertEqual(row["pagination_state"],"UNKNOWN_API_OR_SCHEMA_ERROR")
        self.assertEqual(row["api_error_class"],"rate_limited")
        self.assertEqual(row["pages_requested"],1)

    def test_run_stops_after_first_bad_batch_not_60_more_requests(self):
        row={"wallet_prefix":"0x123","stage":"recent_1h",
             "pagination_state":"UNKNOWN_API_OR_SCHEMA_ERROR",
             "pages_requested":1,"reported_buy_rows_nonunique":0,
             "unexpected_other_wallet_rows":0,"api_error_class":"rate_limited",
             "per_page_latency_seconds":[.1]}
        with mock.patch.object(c,"check",return_value=row) as f:
            result=c.run(report(),now=NOW,clock=clock)
        self.assertEqual(f.call_count,6)
        self.assertTrue(result["stopped_early_for_safety"])
        self.assertEqual(result["not_started_due_to_protection"],54)
        self.assertEqual(result["actual_v2_page_requests"],6)
        self.assertFalse(result["production_state_updated"])

    def test_latency_percentiles_and_error_denominator(self):
        def row(name,state,times,err=None):
            return {"stage":name,"pagination_state":state,
                    "pages_requested":len(times),
                    "reported_buy_rows_nonunique":3,
                    "unexpected_other_wallet_rows":0,
                    "api_error_class":err,
                    "per_page_latency_seconds":times}
        data=c.make_report([
            row("recent_1h","COMPLETE_QUERY_WINDOW",[.1,.5]),
            row("sampled_busy_6h","UNKNOWN_PAGE_LIMIT",[1,3],"")
        ],[("a",3600,"recent_1h"),("b",21600,"sampled_busy_6h")],4,False,NOW)
        self.assertEqual(data["complete_query_windows"],1)
        self.assertEqual(data["incomplete_query_windows"],1)
        self.assertEqual(data["multipage_wallets_observed"],2)
        self.assertEqual(data["actual_v2_page_requests"],4)
        self.assertEqual(data["p95_page_latency_seconds"],3)
        self.assertEqual(data["median_page_latency_seconds"],.75)
        self.assertEqual(data["total_buy_rows_in_complete_windows"],3)
        self.assertTrue(data["projected_1000_wallet_throughput_not_verified"])

    def test_malformed_envelope_is_error_not_valid_empty_list(self):
        row=c.check(wallet(9),3600,"recent_1h",NOW,api=lambda *a,**k: {"data":[]},clock=clock)
        self.assertEqual(row["pagination_state"],"UNKNOWN_API_OR_SCHEMA_ERROR")
        self.assertEqual(row["api_error_class"],"ValueError")


if __name__=="__main__":
    unittest.main()
