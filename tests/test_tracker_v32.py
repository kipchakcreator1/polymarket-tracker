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

    def test_lower_price_not_filtered(self):
        sample = {'pending': {'w|c': {
            'wallet': 'w', 'condition': 'c', 'first': 100, 'last': 100,
            'outcomes': {'t': {'buy_usd': 60, 'buy_qty': 100,
                               'sell_usd': 0, 'sell_qty': 0, 'count': 1}},
        }}, 'tracking': {}}
        _, _, removed = v3.candidate_groups(sample, 200)
        self.assertNotIn('high_buy_price', removed)

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
