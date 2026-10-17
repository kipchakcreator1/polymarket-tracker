"""Marketlens L2 should never substitute future snapshots or unrelated UFC."""
import unittest
from tools import v33_marketlens_archive as m

class MarketLensFreeTests(unittest.TestCase):
    def test_book_nearest_prior_never_future(self):
        p=[(100000,.35,.40),(115000,.36,.41)]
        self.assertIsNone(m.book_asof(p,99000))
        self.assertEqual(m.book_asof(p,110000),(100000,.35,.40))
        self.assertEqual(m.book_asof(p,115000),(115000,.36,.41))
        self.assertIsNone(m.book_asof(p,300000))
    def test_yes_and_no_best_asks_correctly_derived(self):
        p=[(100000,.35,.4)]
        yes=m.quote_buy(p,100001,0)
        no=m.quote_buy(p,100001,1)
        self.assertAlmostEqual(yes["best_ask"],.4)
        self.assertAlmostEqual(no["best_ask"],.65)
        self.assertAlmostEqual(no["best_bid"],.6)
        self.assertTrue(no["not_depth_aware"])
    def test_unknown_side_and_crossed_spread_refused(self):
        self.assertIsNone(m.quote_buy([(100000,.35,.40)],100000,999))
        self.assertIsNone(m.quote_buy([(100000,.35,.40)],100000,True))
        self.assertIsNone(m.quote_buy([(100000,.45,.40)],100000,0))
    def test_parser_drops_invalid_quotes(self):
        rows=[{"market_id":"x","t_ms":"1234","best_bid":"0.5","best_ask":"0.6"},
              {"market_id":"x","t_ms":"1235","best_bid":"0.9","best_ask":"0.6"},
              {"market_id":"x","t_ms":"1236","best_bid":"bad","best_ask":"0.6"}]
        result=m.snapshot_books(rows)
        self.assertEqual(result["x"],[(1234,.5,.6)])
    def test_no_wallet_condition_match_does_not_fabricate_success(self):
        import tempfile,csv,pathlib
        with tempfile.TemporaryDirectory() as folder:
            folder=pathlib.Path(folder)
            (folder/"markets.csv").write_text(
                "market_id,condition_id,resolved_at\n"
                "uuid1,0x"+"1"*64+",2026-08-16T00:00:00Z\n")
            (folder/"snapshots.csv").write_text(
                "market_id,t_ms,best_bid,best_ask\n"
                "uuid1,1786900000000,0.4,0.6\n")
            from unittest.mock import patch
            with patch.object(m.truth,"fetch_wallet_trades",return_value=([],True)),\
                 patch.object(m.truth,"candidate_positions",return_value=[]):
                x=m.evaluate(data_dir=folder)
            self.assertEqual(x["exact_condition_overlaps"],0)
            self.assertEqual(x["tested_wallet_placements"],0)
            self.assertIsNone(x["execution_accurate_profit"])
            self.assertTrue(x["control_quote"]["synthetic_control_not_wallet_entry"])
if __name__=="__main__":
    unittest.main()
