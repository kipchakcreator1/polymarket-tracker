"""Wide market discovery stays bounded and never mistakes activity for skill."""
import unittest
from unittest.mock import patch
import tools.sweep_market_wallets_v32 as wide

W='0x'+'a'*40
C='0x'+'1'*64


class MarketDiscoverySweepTests(unittest.TestCase):
    def test_lists_only_active_distinct_valid_market_ids(self):
        page=[{'conditionId':C,'closed':False,'active':True,'volume24hr':5},
              {'conditionId':C,'closed':False,'active':True,'volume24hr':5},
              {'conditionId':'bad','closed':False,'active':True},
              {'conditionId':'0x'+'2'*64,'closed':True,'active':False}]
        with patch.object(wide.v2,'request_json',return_value=page):
            found,pool,errors=wide.find_markets(2000000000)
        self.assertEqual(len(found),1)
        self.assertEqual(pool,1)
        self.assertEqual(errors,0)

    def test_market_wallets_filtered_by_timestamp_and_condition(self):
        now=2000000000
        payload={'data':[
            {'proxy_wallet':W,'condition_id':C,'timestamp':now},
            {'proxy_wallet':W,'condition_id':'0x'+'2'*64,'timestamp':now},
            {'proxy_wallet':'0x'+'b'*40,'condition_id':C,'timestamp':now-10*86400},
        ]}
        with patch.object(wide.v2,'request_json',return_value=payload):
            rows,wallets=wide.inspect_market(C,now)
        self.assertEqual(rows,3)
        self.assertEqual(wallets,{W})

    def test_wallet_score_not_fabricated_if_missing(self):
        with patch.object(wide.v2,'closed_stats',return_value={
                'n':50,'wins':42,'winrate':.84,
                'recent_realized_pnl':200,'profit_factor':None}):
            result=wide.inspect_new_wallet(W)
        self.assertIsNone(result['profit_factor'])
        self.assertEqual(result['n'],50)


if __name__=='__main__':
    unittest.main()
