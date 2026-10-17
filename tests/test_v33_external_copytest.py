"""Keep Polymarket Paper's useful lag approach, remove look-ahead/bad win-rate."""
import unittest
from tools import v33_external_copytest as b
W="0x"+"a"*40
C="0x"+"b"*64

def row(direction="win",entry=100000):
    return {"condition":C,"token_id":"123456789",
            "league":"UFC","event_slug":"ufc-test-match",
            "first_entry_at":entry,"resolved_at":entry+3600,
            "direction":direction,"source_first_buy_price":.5}
def source(events=None,unknown=2):
    return {"updated_at":123,"wallets":{W:{
        "certified_sample":False,
        "unverified_final_or_mapping_count":unknown,
        "position_truth":{"certified_sample":False,
            "resolved_sports_sample":1,"events":events or [row()]}}}}

class LagCopyTest(unittest.TestCase):
    def test_reject_future_price_even_if_closer_than_past(self):
        h=[(100, .5),(159,.91)]
        self.assertEqual(b.last_known_asof(h,130),(100,.5))
        self.assertIsNone(b.last_known_asof([(131,.8)],130))
        self.assertIsNone(b.last_known_asof([(1,.8)],190, max_stale=180)
                          if False else b.last_known_asof([(1,.8)],200, max_stale=180))
    def test_unsorted_and_invalid_points_get_filtered(self):
        history={"history":[{"t":10,"p":.9},{"t":9,"p":.8},
                            {"t":50,"p":0},{"t":20,"p":1},
                            {"t":11,"p":"not_a_price"}]}
        self.assertEqual(b.clean_history(history,0,30),[(9,.8),(10,.9)])
    def test_wrong_shape_not_valid_price_history(self):
        with self.assertRaises(ValueError):
            b.clean_history({"prices":[]},0,40)
    def test_win_not_inferred_from_final_chart_price(self):
        r=b.evaluate(source([row(direction="loss")]), W,
               fetcher=lambda token,first: [
                     (first,.2),(first+60,.9),(first+300,.99),
                     (first+600,.999)])
        self.assertEqual(r["events"][0]["direction_confirmed_by_v2"],"loss")
        self.assertEqual(r["common_cohort_usable_at_all_lags"],1)
        self.assertEqual(r["common_cohort_mean_roi_at_each_lag_no_fees"]["600"],-1.0)
        self.assertEqual(r["actual_copy_fills"],0)
        self.assertIsNone(r["execution_accurate_roi"])
    def test_ending_before_lag_means_market_not_copyable(self):
        x=row()
        x["resolved_at"]=x["first_entry_at"]+40
        r=b.evaluate(source([x]), W,
                     fetcher=lambda token,first: [(first-.5,.6)] )
        self.assertEqual(r["events"][0]["lags_seconds"]["60"]["status"],
                         "market_already_settled")
    def test_sparse_history_denominator_not_cherry_picked(self):
        e1=row("win",100000)
        e2=row("loss",105000)
        e2["condition"]="0x"+"a"*64
        e2["event_slug"]="ufc-other-match"
        def fetch(token,first):
            return [(100000,.4)] if first==100000 else []
        result=b.evaluate(source([e1,e2]),W,fetcher=fetch)
        self.assertEqual(result["events_examined"],2)
        self.assertEqual(result["lagged_reference_price_summary"]["0"]
                          ["events_with_fresh_historical_price"],1)
        self.assertEqual(result["lagged_reference_price_summary"]["0"]
                          ["coverage_fraction_of_examined"],.5)
        self.assertEqual(result["common_cohort_usable_at_all_lags"],0)
    def test_price_stress_reduces_winner_return(self):
        r=b.evaluate(source([row()]),W,
             fetcher=lambda token,first:[(first,.4),(first+60,.4),
                                         (first+300,.4),(first+600,.4)])
        result=r["events"][0]["lags_seconds"]["60"]
        self.assertGreater(result["roi_on_proxy_no_fees"],
                           result["roi_if_5pct_higher_entry_no_fees"])
        self.assertFalse(r["source_outcome_sample_certified"])
if __name__=="__main__":
    unittest.main()
