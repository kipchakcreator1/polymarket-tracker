"""Activity evidence is a research label, never a prediction or PnL."""
import unittest
from unittest.mock import patch

import wallet_forensics_v32 as fx


W1='0x'+'1'*40
W2='0x'+'2'*40


class ForensicsTests(unittest.TestCase):
    def test_complete_activity_counts_merge_split_redeem_separately(self):
        payload={'data':[
            {'proxy_wallet':W1,'type':'MERGE'},
            {'proxy_wallet':W1,'type':'SPLIT'},
            {'proxy_wallet':W1,'type':'REDEEM'},
            {'proxy_wallet':W1,'type':'REDEEM'},
        ],'pagination':{'has_more':False}}
        with patch.object(fx.v2,'request_json',return_value=payload) as req:
            result=fx.activity_profile(W1,now=2000000000)
        self.assertEqual(result['merge_rows'],1)
        self.assertEqual(result['split_rows'],1)
        self.assertEqual(result['redeem_rows'],2)
        self.assertTrue(result['mechanical_events_observed'])
        self.assertTrue(result['activity_complete'])
        self.assertEqual(req.call_args.args[0],'/v2/activity')

    def test_redeem_only_is_not_automatically_hedging(self):
        payload={'data':[{'proxy_wallet':W1,'type':'REDEEM'}],
                 'pagination':{'has_more':False}}
        with patch.object(fx.v2,'request_json',return_value=payload):
            result=fx.activity_profile(W1,now=2000000000)
        self.assertFalse(result['mechanical_events_observed'])

    def test_partial_activity_does_not_become_negative_evidence(self):
        payload={'data':[], 'pagination':{'has_more':True}}
        with patch.object(fx.v2,'request_json',return_value=payload):
            with self.assertRaisesRegex(ValueError,'missing cursor'):
                fx.activity_profile(W1,now=2000000000)

    def test_failed_first_wallet_does_not_block_next_wallet_forever(self):
        state={'wallets':{W1:{},W2:{}}}
        picks=[(W1,state['wallets'][W1]),(W2,state['wallets'][W2])]
        def lookup(wallet,now):
            if wallet==W1:
                raise RuntimeError('API unavailable for first wallet')
            return {'activity_checked_at':now,'merge_rows':0,
                    'split_rows':0,'redeem_rows':1,
                    'mechanical_events_observed':False,
                    'activity_complete':True}
        with patch.object(fx,'MAX_PER_PASS',1), \
             patch.object(fx,'activity_profile',side_effect=lookup):
            first=fx.sample_eligible(state,picks,now=2000000000)
            second=fx.sample_eligible(state,picks,now=2000000001)
        self.assertEqual(first['errors'],1)
        self.assertEqual(second['errors'],0)
        self.assertEqual(state['wallets'][W2]['activity_checked_at'],2000000001)
        self.assertNotIn('mechanical_events_observed',state['wallets'][W1])


if __name__=='__main__':
    unittest.main()
