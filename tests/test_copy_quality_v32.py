"""Regression tests for COPY quality controls: all API/Telegram calls mocked."""
import unittest
from unittest.mock import patch

import smart_tracker_v2 as v2
import smart_tracker_v3 as v3
import copy_quality_v32 as quality
import paper_tracker_v31 as paper


def good_stats(**change):
    result = {'n': 50, 'wins': 41, 'winrate': .82,
              'recent_realized_pnl': 220, 'profit_factor': 2.2}
    result.update(change)
    return result


def buy_group(price=.4):
    return {'wallet': '0x' + 'b'*40, 'condition': '0x'+'a'*64,
            'first': 100, 'last': 100, 'title': 'Sample',
            'url': '', 'name': '',
            'outcomes': {'tok': {'label': 'YES', 'buy_usd': 250*price,
                                  'buy_qty': 250., 'sell_usd': 0,
                                  'sell_qty': 0., 'count': 1}}}


def paper_quote(px=.43, feasible=True):
    return {'best_ask': px, 'avg_price': px,
            'feasible_1usd': feasible, 'token': 'tok'}


class CopyQualityTests(unittest.TestCase):
    def test_valid_buy_is_only_basic_pass_not_profit_prediction(self):
        ok, reason, facts = quality.assess(
            buy_group(), good_stats(), {'tok': 250}, paper_quote())
        self.assertTrue(ok)
        self.assertEqual(reason, 'passed_quality_checks_not_profit_guarantee')
        self.assertAlmostEqual(facts['copy_price_rise'], .03)

    def test_winrate_alone_does_not_suffice(self):
        ok, reason, _ = quality.assess(
            buy_group(), good_stats(profit_factor=.7, winrate=.94),
            {'tok': 250}, paper_quote())
        self.assertFalse(ok)
        self.assertEqual(reason, 'wallet_gain_loss_ratio_too_low')

    def test_missing_or_too_little_history_is_blocked(self):
        for meta in (good_stats(profit_factor=None), good_stats(n=4), {}):
            with self.subTest(meta=meta):
                ok, _, _ = quality.assess(
                    buy_group(), meta, {'tok': 250}, paper_quote())
                self.assertFalse(ok)

    def test_absolute_copy_price_jump_is_blocked(self):
        ok, reason, facts = quality.assess(
            buy_group(.4), good_stats(), {'tok': 250}, paper_quote(.57))
        self.assertFalse(ok)
        self.assertEqual(reason, 'copy_price_moved_too_far')
        self.assertAlmostEqual(facts['copy_price_rise'], .17)

    def test_relative_copy_price_jump_is_blocked(self):
        ok, reason, _ = quality.assess(
            buy_group(.1), good_stats(), {'tok': 250}, paper_quote(.13))
        self.assertFalse(ok)
        self.assertEqual(reason, 'copy_price_relative_rise_too_large')

    def test_hedged_or_unknown_holdings_blocked(self):
        for holdings in (None, {}, {'tok': 250, 'other': 12}):
            with self.subTest(holdings=holdings):
                ok, _, _ = quality.assess(
                    buy_group(), good_stats(), holdings, paper_quote())
                self.assertFalse(ok)

    def test_unaffordable_one_dollar_order_not_actionable(self):
        ok, reason, _ = quality.assess(
            buy_group(), good_stats(), {'tok': 250},
            paper_quote(.43, feasible=False))
        self.assertFalse(ok)
        self.assertEqual(reason, 'one_usd_below_minimum_or_unknown')

    def test_double_sided_source_is_not_clean_buy(self):
        g = buy_group()
        g['outcomes']['other'] = dict(g['outcomes']['tok'])
        ok, reason, _ = quality.assess(
            g, good_stats(), {'tok': 250}, paper_quote())
        self.assertFalse(ok)
        self.assertEqual(reason, 'not_single_directional_buy')

    def test_score_contains_gain_loss_ratio_without_fake_roi(self):
        rows = [{'realizedPnl': 30}, {'realizedPnl': -20},
                {'realizedPnl': 0}]
        with patch.object(v2, 'request_json', return_value=rows):
            result = v2.closed_stats('0x'+'b'*40)
        self.assertEqual(result['n'], 3)
        self.assertEqual(result['wins'], 1)
        self.assertEqual(result['profit_factor'], 1.5)
        self.assertEqual(result['gross_losses'], 20)
        self.assertNotIn('roi', result)

    def test_monitor_collects_deduplicated_price_profile(self):
        g = buy_group(.99)
        state = {'seen':{}, 'pending':{}, 'wallets':{g['wallet']: {}}}
        t = {'proxy_wallet':g['wallet'], 'condition_id':g['condition'],
             'timestamp':100, 'token_id':'tok','side':'BUY',
             'size':100,'price':.99,'title':'Sample',
             'transaction_hash':'0x'+'f'*64}
        self.assertTrue(v3.add_fill(state,t,200))
        self.assertFalse(v3.add_fill(state,t,200))
        meta=state['wallets'][g['wallet']]
        self.assertEqual(meta['observed_buy_fills'],1)
        self.assertEqual(meta['observed_expensive_buy_fills'],1)

    def test_integration_late_buy_never_sends_or_commits(self):
        g=buy_group(.4)
        gid=v3.group_id(g['wallet'],g['condition'])
        state={'pending':{gid:g},'tracking':{},
               'wallets':{g['wallet']:good_stats()}}
        q=paper_quote(.60)
        with patch.object(v3,'COPY_QUALITY_GATE',True), \
             patch.object(v3,'market_accepts_orders',return_value=True), \
             patch.object(v3,'get_positions',
                          return_value={g['condition']:{'tok':250}}), \
             patch.object(v3.paper,'prepare',return_value=(q,None)), \
             patch.object(v3.v2,'send_telegram') as send, \
             patch.object(v3.paper,'commit') as commit:
            result=v3.dispatch(state,200)
        self.assertEqual(result['sent'],0)
        self.assertNotIn(gid,state['pending'])
        send.assert_not_called()
        commit.assert_not_called()
        self.assertIn(gid,state['tracking'])
        reasons=[r['reason'] for r in
                 state['suppressed_v32']['records'].values()]
        self.assertEqual(reasons,['quality_copy_price_moved_too_far'])

    def test_integration_good_quote_commits_only_after_send(self):
        g=buy_group(.4)
        gid=v3.group_id(g['wallet'],g['condition'])
        state={'pending':{gid:g},'tracking':{},
               'wallets':{g['wallet']:good_stats()}}
        q=paper_quote(.43)
        with patch.object(v3,'COPY_QUALITY_GATE',True), \
             patch.object(v3,'market_accepts_orders',return_value=True), \
             patch.object(v3,'get_positions',
                          return_value={g['condition']:{'tok':250}}), \
             patch.object(v3.paper,'prepare',return_value=(q,None)), \
             patch.object(v3.v2,'send_telegram') as send, \
             patch.object(v3.paper,'commit') as commit:
            result=v3.dispatch(state,200)
        self.assertEqual(result['sent'],1)
        send.assert_called_once()
        commit.assert_called_once()
        self.assertEqual(q['quality_v32']['profit_factor'],2.2)

    def test_paper_records_source_and_copy_price_separately(self):
        g=buy_group(.4)
        fake_market={'closed':False,'acceptingOrders':True}
        quote={'price':.44,'shares':2.272727,'best_ask':.43,
               'min_order_shares':1,'one_dollar_meets_min':True}
        with patch.object(paper,'get_market',return_value=fake_market), \
             patch.object(paper,'market_token_index',return_value=0), \
             patch.object(paper,'book_simulation',return_value=quote):
            record,_=paper.prepare({},g,'BUY',{'tok':250},200)
        self.assertAlmostEqual(record['source_avg_buy_price'],.4)
        self.assertAlmostEqual(record['source_to_paper_price_change'],.04)


if __name__=='__main__':
    unittest.main()
