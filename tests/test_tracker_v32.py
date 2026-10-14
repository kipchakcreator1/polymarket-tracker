"""Run from repository root: python -m unittest discover -s tests."""
import unittest
from unittest.mock import patch

import smart_tracker_v3 as v3
import wallet_discovery_v32 as discovery


class Tracker32Tests(unittest.TestCase):
    def test_public_addresses_only(self):
        condition = '0x' + 'a' * 64
        wallet = '0x' + 'b' * 40
        result = discovery.extract_wallets([
            {'condition_id': condition, 'proxy_wallet': wallet.upper().replace('0X', '0x'), 'timestamp': 2000000000},
            {'condition_id': '0x' + 'c' * 64, 'proxy_wallet': '0x' + 'd' * 40, 'timestamp': 2000000000},
            {'condition_id': condition, 'proxy_wallet': 'private-user', 'timestamp': 2000000000},
        ], condition, now=2000000000)
        self.assertEqual(result, {wallet})

    def test_high_buy_price_dropped(self):
        sample = {'pending': {'w|c': {
            'wallet': 'w', 'condition': 'c', 'first': 100, 'last': 100,
            'outcomes': {'t': {'buy_usd': 99.9, 'buy_qty': 100,
                               'sell_usd': 0, 'sell_qty': 0, 'count': 1}},
        }}, 'tracking': {}}
        _, _, removed = v3.candidate_groups(sample, 200)
        self.assertEqual(removed.get('high_buy_price'), 1)
        recorded = list(sample['suppressed_v32']['records'].values())
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]['reason'], 'source_avg_buy_at_or_above_limit')
        self.assertEqual(recorded[0]['source_avg_buy'], 0.999)
        self.assertFalse(recorded[0]['telegram_sent'])
        self.assertFalse(recorded[0]['paper_stake_created'])
        self.assertEqual(sample['tracking']['w|c'], 200)

    def test_lower_price_not_filtered(self):
        sample = {'pending': {'w|c': {
            'wallet': 'w', 'condition': 'c', 'first': 100, 'last': 100,
            'outcomes': {'t': {'buy_usd': 60, 'buy_qty': 100,
                               'sell_usd': 0, 'sell_qty': 0, 'count': 1}},
        }}, 'tracking': {}}
        _, _, removed = v3.candidate_groups(sample, 200)
        self.assertNotIn('high_buy_price', removed)

    def test_high_ask_suppressed_without_telegram_or_paper(self):
        condition = '0x' + 'a'*64
        g = {'wallet': '0x'+'b'*40, 'condition': condition,
             'first': 130, 'last': 130, 'title': 'Example',
             'outcomes': {'tok': {'label': 'Yes', 'buy_usd': 50,
                                  'buy_qty': 100, 'sell_usd': 0,
                                  'sell_qty': 0, 'count': 1}}}
        state = {'pending': {v3.group_id(g['wallet'], condition): g},
                 'tracking': {}, 'wallets': {}}
        quote = {'best_ask': 0.99, 'avg_price': 0.992}
        with patch.object(v3, 'market_accepts_orders', return_value=True), \
             patch.object(v3, 'get_positions',
                          return_value={condition: {'tok': 100}}), \
             patch.object(v3.paper, 'prepare', return_value=(quote, 'Paper quote')), \
             patch.object(v3.v2, 'send_telegram') as send, \
             patch.object(v3.paper, 'commit') as commit:
            result = v3.dispatch(state, now=200)
        self.assertEqual(result['sent'], 0)
        self.assertEqual(len(state['pending']), 0)
        send.assert_not_called()
        commit.assert_not_called()
        observed = list(state['suppressed_v32']['records'].values())
        self.assertEqual(observed[0]['quote_best_ask'], 0.99)
        self.assertEqual(observed[0]['reason'], 'current_ask_at_or_above_limit')
        self.assertIn(v3.group_id(g['wallet'], condition), state['tracking'])

    def test_suppression_preserves_v31_paper_history(self):
        original_paper = {'records': {'past|market': {'status': 'OPEN'}}}
        state = {'tracking': {}, 'paper_v31': original_paper}
        group = {'wallet': 'w', 'condition': 'c', 'last': 100,
                 'outcomes': {'t': {'label': 'Yes', 'buy_usd': 99,
                                     'buy_qty': 100}}}
        v3.remember_suppressed_buy(state, group, now=200,
                                   reason='source_avg_buy_at_or_above_limit')
        self.assertIs(state['paper_v31'], original_paper)
        self.assertEqual(len(original_paper['records']), 1)
        self.assertEqual(len(state['suppressed_v32']['records']), 1)


    def test_bounded_discovery_and_preserves_existing_wallet(self):
        condition = '0x' + 'a'*64
        existing = '0x' + '1'*40
        new = '0x' + '2'*40
        state = {'wallets': {existing: {'winrate': 0.9}}}
        def fake_request(path, params, base=None):
            if path == '/markets':
                return [{'conditionId': condition, 'active': True, 'closed': False, 'volume24hr': 200}]
            if path == '/v2/trades':
                return {'data': [{'condition_id': condition, 'proxy_wallet': new, 'timestamp': 2000000000}]}
            raise AssertionError(path)
        with patch.object(discovery.v2, 'request_json', side_effect=fake_request):
            outcome = discovery.discover_market_wallets(state, now=2000000000)
        self.assertEqual(outcome['added'], 1)
        self.assertEqual(state['wallets'][existing]['winrate'], 0.9)
        self.assertIn(new, state['wallets'])


if __name__ == '__main__':
    unittest.main()
