"""Shadow preview must never count incomplete wallet trade pages as real signals."""
import unittest
from unittest.mock import patch
import tools.shadow_qualified_v32 as shadow

WALLET='0x'+'a'*40


class ShadowPreviewTests(unittest.TestCase):
    def test_rank_stratified_sample_without_duplicate(self):
        wallets=[(str(i),{}) for i in range(75)]
        sampled=shadow.sample_ranked(wallets,12)
        self.assertEqual(len(sampled),12)
        self.assertEqual(len(set(x[0] for x in sampled)),12)

    def test_wallet_trade_mismatch_rejected(self):
        body={'data':[{'proxy_wallet':'0x'+'b'*40,'side':'BUY'}],
              'pagination':{'has_more':False}}
        with patch.object(shadow.v2,'request_json',return_value=body):
            with self.assertRaisesRegex(ValueError,'wallet mismatch'):
                shadow.fetch_recent(WALLET,100,200)

    def test_incomplete_cursor_rejected(self):
        body={'data':[],'pagination':{'has_more':True}}
        with patch.object(shadow.v2,'request_json',return_value=body):
            with self.assertRaisesRegex(ValueError,'Incomplete trade cursor'):
                shadow.fetch_recent(WALLET,100,200)

    def test_complete_fetch_filters_non_trade_activity(self):
        body={'data':[{'proxy_wallet':WALLET,'side':'BUY'},
                      {'proxy_wallet':WALLET,'side':'MERGE'}],
              'pagination':{'has_more':False}}
        with patch.object(shadow.v2,'request_json',return_value=body):
            rows=shadow.fetch_recent(WALLET,100,200)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['side'],'BUY')


if __name__=='__main__':
    unittest.main()
