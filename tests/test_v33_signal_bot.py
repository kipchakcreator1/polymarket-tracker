"""Offline tests: no real Telegram calls, trades, API requests or credentials."""
import unittest
from unittest import mock

from tools import v33_signal_bot as bot

W = "0x" + "a"*40
COND = "0x" + "b"*64
TOKEN = "1234"
NOW = 1000000


def trade(price=.6, timestamp=NOW, side="BUY", outcome="Yes"):
    return {"proxy_wallet": W, "condition_id": COND, "token_id": TOKEN,
            "side": side, "timestamp": timestamp, "size": 200,
            "price": price, "outcome": outcome, "title": "Test event",
            "transaction_hash": "test-hash"}


class BotTests(unittest.TestCase):
    def test_secret_validation_keeps_distinct_tokens(self):
        with self.assertRaises(ValueError):
            bot.validate_route({"TELEGRAM_V33_BOT_TOKEN": "abc",
                                "TELEGRAM_V31_BOT_TOKEN": "abc",
                                "TELEGRAM_V32_BOT_TOKEN": "def",
                                "TELEGRAM_CHAT_ID": "12345678"})
        with mock.patch.dict(bot.os.environ, {}, clear=True):
            bot.validate_route({"TELEGRAM_V33_BOT_TOKEN": "new",
                                "TELEGRAM_V31_BOT_TOKEN": "old1",
                                "TELEGRAM_V32_BOT_TOKEN": "old2",
                                "TELEGRAM_CHAT_ID": "12345678"})
            self.assertEqual(bot.os.environ["TELEGRAM_BOT_TOKEN"], "new")

    def test_valid_shortlist_is_bounded(self):
        row = {"wallet": W, "closed": 90,
               "profitable_closed_rate": .84, "realized_closed_pnl": 1000,
               "profit_factor": 2.5, "name": "Tester"}
        data = {"schema": "v33_wallet_shortlist_1", "observed_at": NOW,
                "shortlist": [row]}
        self.assertEqual(list(bot.select_wallets(data, NOW)), [W])
        with self.assertRaises(ValueError):
            bot.select_wallets(data, NOW+4*86400)

    def test_ninetynine_cent_buy_excluded(self):
        state = bot.blank_state()
        self.assertTrue(bot.add_fill(state, trade(price=.99), NOW))
        group = next(iter(state["pending"].values()))
        cand, reason = bot.candidate(group, NOW)
        self.assertIsNone(cand)
        self.assertEqual(reason, "source_price_excluded")

    def test_midprice_buy_allowed_and_deduped(self):
        state = bot.blank_state()
        self.assertTrue(bot.add_fill(state, trade(price=.5), NOW))
        self.assertFalse(bot.add_fill(state, trade(price=.5), NOW))
        group = next(iter(state["pending"].values()))
        cand, reason = bot.candidate(group, NOW)
        self.assertIsNone(reason)
        self.assertAlmostEqual(cand["source"], .5)
        self.assertGreaterEqual(cand["usd"], 50)

    def test_two_sides_are_not_clean_buy(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        other = trade(price=.5, outcome="No")
        other["token_id"] = "other"
        bot.add_fill(state, other, NOW)
        group = next(iter(state["pending"].values()))
        cand, reason = bot.candidate(group, NOW)
        self.assertIsNone(cand)
        self.assertEqual(reason, "mixed_or_sell")

    def test_verification_fail_closed(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(None, "book_no_liquidity")), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            n = bot.deliver(state, {W: {"name": "Tester", "winrate": .88}}, NOW)
        self.assertEqual(n, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["book_no_liquidity"], 1)

    def test_delivery_sends_once(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        meta = {W: {"name": "Tester", "winrate": .88}}
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            n = bot.deliver(state, meta, NOW)
            self.assertEqual(n, 1)
            sender.assert_called_once()
            bot.add_fill(state, dict(trade(price=.5), transaction_hash="different"), NOW)
            n2 = bot.deliver(state, meta, NOW)
        self.assertEqual(n2, 0)


    def test_user_priority_keeps_qualified_wallet_with_small_cap(self):
        requested = "0x" + "c"*40
        base = {"closed": 100, "profitable_closed_rate": .72,
                "realized_closed_pnl": 850, "profit_factor": 2.2}
        data = {"schema": "v33_wallet_shortlist_1", "observed_at": NOW,
                "shortlist": [dict(base, wallet=W),
                              dict(base, wallet=requested)]}
        with mock.patch.object(bot, "MAX_WALLETS", 1), \
             mock.patch.object(bot, "PRIORITY_WALLETS", frozenset([requested])):
            self.assertEqual(list(bot.select_wallets(data, NOW)), [requested])

    def test_user_priority_does_not_bypass_wallet_quality(self):
        requested = "0x" + "c"*40
        data = {"schema": "v33_wallet_shortlist_1", "observed_at": NOW,
                "shortlist": [
                    {"wallet": requested, "closed": 10,
                     "profitable_closed_rate": .99,
                     "realized_closed_pnl": 1000, "profit_factor": 2.0},
                    {"wallet": W, "closed": 90,
                     "profitable_closed_rate": .8,
                     "realized_closed_pnl": 1000, "profit_factor": 2.0}]}
        with mock.patch.object(bot, "MAX_WALLETS", 1), \
             mock.patch.object(bot, "PRIORITY_WALLETS", frozenset([requested])):
            self.assertEqual(list(bot.select_wallets(data, NOW)), [W])

    def test_5m_crypto_signals_take_priority_under_cap(self):
        fast = ("fast", {"wallet": W, "last": NOW-25,
                "url": "https://polymarket.com/event/btc-updown-5m-1792056900"})
        other = ("other", {"wallet": "0x" + "c"*40, "last": NOW,
                 "url": "https://polymarket.com/event/nba-finals"})
        with mock.patch.object(bot, "PRIORITY_WALLETS", frozenset()):
            self.assertEqual(sorted([other, fast], key=bot.signal_sort_key)[0], fast)

    def test_requested_wallet_prioritized_before_other_fast_crypto(self):
        requested = "0x" + "c"*40
        fast = ("fast", {"wallet": W, "last": NOW,
                "url": "https://polymarket.com/event/btc-updown-5m-1792056900"})
        selected = ("user", {"wallet": requested, "last": NOW-10,
                   "url": "https://polymarket.com/event/nba-finals"})
        with mock.patch.object(bot, "PRIORITY_WALLETS", frozenset([requested])):
            self.assertEqual(sorted([fast, selected], key=bot.signal_sort_key)[0], selected)


    def test_seven_recent_losses_are_blocked(self):
        rows = [{"realizedPnl": -2}] * 7 + [{"realizedPnl": 5}] * 13
        result = bot.recent_form(rows)
        self.assertEqual(result["closed"], 20)
        self.assertEqual(result["loss_streak"], 7)
        self.assertAlmostEqual(result["positive_rate"], .65)
        state = bot.blank_state()
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            reason = bot.recent_form_reason(state, W, NOW)
        self.assertEqual(reason, "recent_loss_streak")

    def test_last_twenty_winrate_floor_blocks_poor_performance(self):
        rows = [{"realizedPnl": 2}] * 11 + [{"realizedPnl": -1}] * 9
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            self.assertEqual(
                bot.recent_form_reason(bot.blank_state(), W, NOW),
                "recent_positive_rate_low")

    def test_good_recent_form_is_cached_three_minutes(self):
        rows = [{"realizedPnl": 4}] * 18 + [{"realizedPnl": -2}] * 2
        state = bot.blank_state()
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows) as req:
            self.assertIsNone(bot.recent_form_reason(state, W, NOW))
            self.assertIsNone(bot.recent_form_reason(state, W, NOW + 150))
            self.assertIsNone(bot.recent_form_reason(state, W, NOW + 181))
            self.assertEqual(req.call_count, 2)

    def test_recent_history_api_failure_never_sends(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot.v2, "request_json",
                               side_effect=TimeoutError("offline")), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            count = bot.deliver(state, {W: {"name": "Test", "winrate": .88}}, NOW)
        self.assertEqual(count, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["recent_form_request_error"], 1)
        self.assertEqual(len(state["pending"]), 1)

    def test_recent_seven_losses_stop_alert_even_with_high_old_winrate(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        rows = [{"realizedPnl": -1}] * 7 + [{"realizedPnl": 10}] * 13
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot, "fetch_recent_results", return_value=rows), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            count = bot.deliver(state, {W: {"name": "Test", "winrate": .91}}, NOW)
        self.assertEqual(count, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["recent_loss_streak"], 1)


    def test_recent_history_high_winrate_but_large_losses_is_blocked(self):
        rows = [{"realizedPnl": 1}] * 14 + [{"realizedPnl": -10}] * 6
        state = bot.blank_state()
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            result = bot.recent_form_reason(state, W, NOW)
        self.assertEqual(result, "recent_negative_expectancy_sample")
        self.assertAlmostEqual(state["recent_form_cache"][W]["net_pnl"], -46)

    def test_recent_history_missing_realized_pnl_is_not_zero(self):
        with self.assertRaises(ValueError):
            bot.recent_form([{"wrong_field": "x"}] * 20)

    def test_five_minute_market_must_allow_time_to_act(self):
        start = 1792056900
        url = "https://polymarket.com/event/btc-updown-5m-1792056900"
        group = {"url": url, "last": start + 180}
        self.assertIsNone(bot.fast_market_reason(group, start + 205))
        self.assertEqual(bot.fast_market_reason(group, start + 211),
                         "fast_market_too_late")
        self.assertEqual(bot.fast_market_reason(group, start + 195 + 20),
                         "fast_market_too_late")

    def test_five_minute_trade_stale_even_if_market_still_open(self):
        start = 1792056900
        group = {"url": "https://polymarket.com/event/btc-updown-5m-1792056900",
                 "last": start + 30}
        self.assertEqual(bot.fast_market_reason(group, start + 120),
                         "fast_trade_stale")

    def test_other_markets_not_given_arbitrary_five_minute_cutoff(self):
        self.assertIsNone(bot.fast_market_reason(
            {"url": "https://polymarket.com/event/nba-match",
             "last": NOW - 200}, NOW))

    def test_sharp_price_drop_blocks_example_btc_signal(self):
        group = {"wallet": W, "condition": COND}
        cand = {"token": TOKEN, "source": .78}
        quote = {"price": .65, "best_ask": .65, "spread": .02,
                 "one_dollar_meets_min": True}
        with mock.patch.object(bot.v32, "market_accepts_orders",
                               return_value=True), \
             mock.patch.object(bot.v32, "get_positions",
                               return_value={COND: {TOKEN: 15}}), \
             mock.patch.object(bot.paper, "book_simulation",
                               return_value=quote):
            _, reason = bot.check_trade(group, cand)
        self.assertEqual(reason, "price_fell_sharply")

    def test_wide_bid_ask_spread_rejected(self):
        group = {"wallet": W, "condition": COND}
        cand = {"token": TOKEN, "source": .60}
        quote = {"price": .61, "best_ask": .61, "spread": .10,
                 "one_dollar_meets_min": True}
        with mock.patch.object(bot.v32, "market_accepts_orders",
                               return_value=True), \
             mock.patch.object(bot.v32, "get_positions",
                               return_value={COND: {TOKEN: 15}}), \
             mock.patch.object(bot.paper, "book_simulation",
                               return_value=quote):
            _, reason = bot.check_trade(group, cand)
        self.assertEqual(reason, "spread_too_wide")

    def test_slippage_rejected_even_if_best_ask_is_good(self):
        group = {"wallet": W, "condition": COND}
        cand = {"token": TOKEN, "source": .59}
        quote = {"price": .62, "best_ask": .58, "spread": .02,
                 "one_dollar_meets_min": True}
        with mock.patch.object(bot.v32, "market_accepts_orders",
                               return_value=True), \
             mock.patch.object(bot.v32, "get_positions",
                               return_value={COND: {TOKEN: 15}}), \
             mock.patch.object(bot.paper, "book_simulation",
                               return_value=quote):
            _, reason = bot.check_trade(group, cand)
        self.assertEqual(reason, "book_slippage")

    def test_narrow_book_and_stable_price_pass_all_book_guards(self):
        group = {"wallet": W, "condition": COND}
        cand = {"token": TOKEN, "source": .60}
        quote = {"price": .61, "best_ask": .60, "spread": .02,
                 "one_dollar_meets_min": True}
        with mock.patch.object(bot.v32, "market_accepts_orders",
                               return_value=True), \
             mock.patch.object(bot.v32, "get_positions",
                               return_value={COND: {TOKEN: 15}}), \
             mock.patch.object(bot.paper, "book_simulation",
                               return_value=quote):
            _, reason = bot.check_trade(group, cand)
        self.assertIsNone(reason)

    def test_book_simulation_calculates_actual_spread(self):
        book = {"asset_id": TOKEN, "min_order_size": "1",
                "asks": [{"price": ".61", "size": "20"}],
                "bids": [{"price": ".59", "size": "20"}]}
        with mock.patch.object(bot.v2, "request_json", return_value=book):
            quote = bot.paper.book_simulation(TOKEN, stake=5)
        self.assertAlmostEqual(quote["spread"], .02)
        self.assertAlmostEqual(quote["best_bid"], .59)


    def test_opposite_traders_on_same_market_block_both_signals(self):
        other_wallet = "0x" + "c" * 40
        first = trade(price=.6)
        second = dict(trade(price=.6, outcome="No"),
                      proxy_wallet=other_wallet, token_id="5678",
                      transaction_hash="opposite-hash")
        state = bot.blank_state()
        self.assertTrue(bot.add_fill(state, first, NOW))
        self.assertTrue(bot.add_fill(state, second, NOW))
        selected = {W: {"name": "A", "winrate": .81},
                    other_wallet: {"name": "B", "winrate": .83}}
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade",
                               return_value=({"price": .61}, None)), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            count = bot.deliver(state, selected, NOW)
        self.assertEqual(count, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["conflicting_wallets"], 2)
        self.assertFalse(state["pending"])

    def test_recent_opposing_telegram_alert_blocks_next_opposite(self):
        other_wallet = "0x" + "c" * 40
        state = bot.blank_state()
        state["sent"][other_wallet + "|" + COND + "|5678"] = NOW - 30
        state["pending"]["new"] = {"wallet": W, "condition": COND,
                                   "last": NOW, "outcomes": {}}
        self.assertTrue(bot.conflicting_wallet_signal(
            state, {"wallet": W, "condition": COND},
            {"token": TOKEN}, NOW))
        self.assertEqual(state["conflicting_markets"][COND], NOW)

    def test_old_opposite_signal_does_not_block_forever(self):
        other_wallet = "0x" + "c" * 40
        state = bot.blank_state()
        state["sent"][other_wallet + "|" + COND + "|5678"] = NOW - 1000
        self.assertFalse(bot.conflicting_wallet_signal(
            state, {"wallet": W, "condition": COND},
            {"token": TOKEN}, NOW))


    def test_active_three_red_positions_block_even_with_good_closed_history(self):
        wallet = W
        state = bot.blank_state()
        red = [
            {"status": "OPEN", "current_size": 100, "entry_cost_usdc": 100,
             "unrealized_pnl": -20, "redeemable": False},
            {"status": "OPEN", "current_size": 100, "entry_cost_usdc": 100,
             "unrealized_pnl": -10, "redeemable": False},
            {"status": "OPEN", "current_size": 100, "entry_cost_usdc": 100,
             "unrealized_pnl": -10, "redeemable": False}]
        response = {"data": red,
                    "pagination": {"has_more": False, "next_cursor": None}}
        with mock.patch.object(bot.v2, "request_json", return_value=response):
            reason = bot.active_form_reason(state, wallet, NOW)
        self.assertEqual(reason, "active_most_positions_red")
        self.assertEqual(state["active_form_cache"][wallet]["red_count"], 3)

    def test_active_big_unrealized_loss_rejected_even_one_position(self):
        pos = [{"status": "OPEN", "redeemable": False,
                "current_size": 500, "entry_cost_usdc": 200,
                "unrealized_pnl": -60}]
        out = bot.active_form(pos)
        self.assertEqual(out["active_count"], 1)
        self.assertAlmostEqual(out["unrealized_roi"], -.30)
        with mock.patch.object(bot, "fetch_open_positions", return_value=pos):
            self.assertEqual(bot.active_form_reason(bot.blank_state(), W, NOW),
                             "active_portfolio_drawdown")

    def test_active_positive_portfolio_remains_eligible(self):
        good = [
            {"status": "OPEN", "redeemable": False, "current_size": 100, "entry_cost_usdc": 100,
             "unrealized_pnl": 12},
            {"status": "OPEN", "redeemable": False, "current_size": 100, "entry_cost_usdc": 100,
             "unrealized_pnl": 8},
            {"status": "OPEN", "redeemable": False, "current_size": 100, "entry_cost_usdc": 100,
             "unrealized_pnl": -2}]
        with mock.patch.object(bot, "fetch_open_positions", return_value=good):
            self.assertIsNone(bot.active_form_reason(bot.blank_state(), W, NOW))

    def test_active_resolved_positions_are_not_marked_live(self):
        rows = [
            {"status": "REDEEMABLE", "redeemable": True,
             "current_size": 500, "entry_cost_usdc": 100,
             "unrealized_pnl": -100},
            {"status": "OPEN", "redeemable": False,
             "current_size": 100, "entry_cost_usdc": 100,
             "unrealized_pnl": 8}]
        form = bot.active_form(rows)
        self.assertEqual(form["active_count"], 1)
        self.assertEqual(form["red_count"], 0)

    def test_active_missing_pnl_is_unverified_not_zero(self):
        rows = [{"status": "OPEN", "redeemable": False, "current_size": 100,
                 "entry_cost_usdc": 100}]
        with self.assertRaises(ValueError):
            bot.active_form(rows)

    def test_active_missing_status_metadata_fails_closed(self):
        rows = [{"status": "OPEN", "current_size": 100,
                 "entry_cost_usdc": 100, "unrealized_pnl": -50}]
        with self.assertRaises(ValueError):
            bot.active_form(rows)

    def test_active_pagination_must_be_complete(self):
        first = {"data": [], "pagination":
                 {"has_more": True, "next_cursor": "next-page"}}
        second = {"data": [], "pagination":
                  {"has_more": False, "next_cursor": None}}
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=[first, second]) as req:
            self.assertEqual(bot.fetch_open_positions(W), [])
        self.assertEqual(req.call_count, 2)
        self.assertEqual(req.call_args[0][0], "/v2/positions")

    def test_active_pagination_missing_cursor_fails_closed(self):
        bad = {"data": [], "pagination":
               {"has_more": True, "next_cursor": None}}
        with mock.patch.object(bot.v2, "request_json", return_value=bad):
            with self.assertRaises(ValueError):
                bot.fetch_open_positions(W)

    def test_active_api_failure_sends_no_signal(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.6), NOW)
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .61, "best_ask": .60, "spread": .02}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "request_json",
                               side_effect=TimeoutError("public API unavailable")), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            count = bot.deliver(
                state, {W: {"name": "Trader", "winrate": .93}}, NOW)
        self.assertEqual(count, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["active_form_request_error"], 1)
        self.assertEqual(len(state["pending"]), 1)

    def test_active_cache_is_short_and_keeps_api_load_bounded(self):
        positive = [{"status": "OPEN", "redeemable": False, "current_size": 100,
                     "entry_cost_usdc": 100, "unrealized_pnl": 20}]
        state = bot.blank_state()
        with mock.patch.object(bot, "fetch_open_positions",
                               return_value=positive) as fetcher:
            self.assertIsNone(bot.active_form_reason(state, W, NOW))
            self.assertIsNone(bot.active_form_reason(state, W, NOW + 40))
            self.assertIsNone(bot.active_form_reason(state, W, NOW + 65))
        self.assertEqual(fetcher.call_count, 2)


    def test_politics_title_is_blocked_before_calling_api(self):
        g = {"condition": COND, "wallet": W,
             "title": "Will Trump win the election?",
             "url": "https://polymarket.com/event/trump-us-election"}
        with mock.patch.object(bot.v2, "request_json") as request:
            reason = bot.political_market_reason(bot.blank_state(), g, NOW)
        self.assertEqual(reason, "political_market")
        request.assert_not_called()

    def test_politics_tag_label_is_recognized_even_without_political_slug(self):
        self.assertTrue(bot.political_labels(
            {"question": "Will it happen?",
             "tags": [{"label": "Politics", "slug": "general"}]}))

    def test_politics_hidden_in_parent_event_tags_is_blocked(self):
        g = {"condition": COND, "wallet": W,
             "title": "Will it happen?",
             "url": "https://polymarket.com/event/special-2026"}
        market = {"conditionId": COND, "question": "Will it happen?",
                  "events": [{"slug": "special-2026"}]}
        event = {"slug": "special-2026", "title": "Special event",
                 "tags": [{"slug": "politics", "label": "Politics"}]}
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=[[market], [event]]) as fetch:
            state = bot.blank_state()
            reason = bot.political_market_reason(state, g, NOW)
        self.assertEqual(reason, "political_market")
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(state["politics_cache"][COND]["political"])

    def test_crypto_event_category_passes_without_politics(self):
        g = {"condition": COND, "wallet": W,
             "title": "Bitcoin Up or Down - October 15",
             "url": "https://polymarket.com/event/btc-updown-5m-1792056900"}
        market = {"conditionId": COND, "question": "Bitcoin Up or Down"}
        event = {"slug": "btc-updown-5m-1792056900", "title": "Bitcoin Up or Down",
                 "tags": [{"slug": "crypto", "label": "Crypto"}]}
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=[[market], [event]]) as fetch:
            state = bot.blank_state()
            self.assertIsNone(bot.political_market_reason(state, g, NOW))
            self.assertIsNone(bot.political_market_reason(state, g, NOW + 120))
        self.assertEqual(fetch.call_count, 2)

    def test_sports_event_category_passes_without_politics(self):
        g = {"condition": COND, "wallet": W,
             "title": "Lakers vs Celtics",
             "url": "https://polymarket.com/event/nba-lakers-celtics"}
        market = {"conditionId": COND, "question": "Lakers vs Celtics"}
        event = {"slug": "nba-lakers-celtics", "title": "Lakers vs Celtics",
                 "tags": [{"slug": "sports", "label": "Sports"}]}
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=[[market], [event]]):
            self.assertIsNone(bot.political_market_reason(
                bot.blank_state(), g, NOW))

    def test_unknown_political_category_metadata_fails_closed(self):
        g = {"condition": COND, "wallet": W, "title": "Will it happen?",
             "url": "https://polymarket.com/event/ambiguous-event"}
        market = {"conditionId": COND, "question": "Will it happen?"}
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=[[market], []]):
            with self.assertRaises(ValueError):
                bot.political_market_reason(bot.blank_state(), g, NOW)

    def test_politics_alert_is_skipped_before_copy_price_checks(self):
        state = bot.blank_state()
        row = trade(price=.6)
        row["title"] = "Who will win the presidential election?"
        row["event_slug"] = "us-presidential-election"
        bot.add_fill(state, row, NOW)
        with mock.patch.object(bot, "check_trade") as quote_check, \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            count = bot.deliver(
                state, {W: {"name": "Trader", "winrate": .91}}, NOW)
        self.assertEqual(count, 0)
        sender.assert_not_called()
        quote_check.assert_not_called()
        self.assertEqual(state["counters"]["political_market"], 1)

    def test_politics_cache_notifies_again_after_expiration(self):
        g = {"condition": COND, "wallet": W,
             "title": "Lakers vs Celtics",
             "url": "https://polymarket.com/event/nba-lakers-celtics"}
        market = {"conditionId": COND, "question": "Lakers vs Celtics",
                  "tags": [{"slug": "sports"}]}
        event = {"slug": "nba-lakers-celtics", "tags": [{"slug": "sports"}]}
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=[[market], [event], [market], [event]]) as r:
            state = bot.blank_state()
            bot.political_market_reason(state, g, NOW)
            bot.political_market_reason(state, g, NOW + 950)
        self.assertEqual(r.call_count, 4)


    def test_recent_v2_combines_closed_and_settled_unredeemed_losses(self):
        now = NOW
        def position(i, status, pnl, last):
            row = {"proxy_wallet": W, "condition_id": COND,
                   "token_id": str(i), "last_event_at": last,
                   "status": status, "realized_pnl": pnl}
            if status == "REDEEMABLE":
                row.update(redeemable=True, unrealized_pnl=pnl)
                row["realized_pnl"] = 0
            return row
        closed = [position(i, "CLOSED", 2, now - i) for i in range(30)]
        lost = [position(100+i, "REDEEMABLE", -20, now-20-i)
                for i in range(7)]
        replies = [
            {"data": closed, "pagination": {"has_more": False}},
            {"data": lost, "pagination": {"has_more": False}}
        ]
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=replies) as req:
            rows = bot.fetch_recent_results(W, now)
        self.assertEqual(req.call_count, 2)
        self.assertEqual(req.call_args_list[0].args[0], "/v2/positions")
        self.assertEqual(req.call_args_list[0].args[1]["status"], "CLOSED")
        self.assertEqual(req.call_args_list[1].args[1]["status"], "REDEEMABLE_LOST")
        self.assertEqual(sum(r["_unredeemed_lost"] for r in rows), 7)
        self.assertEqual(len(rows), 37)
        snap = bot.recent_form(rows, now)
        self.assertEqual(snap["unredeemed_lost_24h"], 7)

    def test_unredeemed_losing_positions_block_previously_good_wallet(self):
        now = NOW
        rows = [{"realizedPnl": 10, "timestamp": now - i,
                 "_now": now} for i in range(20)]
        rows.extend([{"realizedPnl": -20, "timestamp": now - 40 - i,
                      "_now": now, "_unredeemed_lost": True} for i in range(7)])
        state = bot.blank_state()
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            result = bot.recent_form_reason(state, W, now)
        self.assertEqual(result, "recent_unredeemed_loss_cluster")
        self.assertEqual(state["recent_form_cache"][W]["positive_rate"], 1.)

    def test_recent_v2_rejects_incomplete_or_unverifiable_losers(self):
        row = {"proxy_wallet": W, "condition_id": COND, "token_id": TOKEN,
               "last_event_at": NOW, "status": "REDEEMABLE",
               "redeemable": False, "realized_pnl": 0, "unrealized_pnl": -100}
        replies = [{"data": [], "pagination": {"has_more": False}},
                   {"data": [row], "pagination": {"has_more": False}}]
        with mock.patch.object(bot.v2, "request_json", side_effect=replies):
            with self.assertRaises(ValueError):
                bot.fetch_recent_results(W, NOW)

    def test_recent_v2_refuses_unbound_or_bad_api_data(self):
        replies = [{"data": [], "pagination": {"has_more": False}},
                   {"data": [{"wrong_wallet": "anonymous"}],
                    "pagination": {"has_more": False}}]
        with mock.patch.object(bot.v2, "request_json", side_effect=replies):
            with self.assertRaises(ValueError):
                bot.fetch_recent_results(W, NOW)

    def test_recent_v2_dedupes_same_token_from_closed_and_redeemable(self):
        base = {"proxy_wallet": W, "condition_id": COND,
                "token_id": TOKEN, "last_event_at": NOW}
        old = dict(base, status="CLOSED", realized_pnl=100)
        lost = dict(base, status="REDEEMABLE", redeemable=True,
                    realized_pnl=0, unrealized_pnl=-50)
        replies = [{"data": [old], "pagination": {"has_more": False}},
                   {"data": [lost], "pagination": {"has_more": False}}]
        with mock.patch.object(bot.v2, "request_json", side_effect=replies):
            rows = bot.fetch_recent_results(W, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["realizedPnl"], -50)

    def test_recent_cache_invalidates_old_pre_migration_history(self):
        state = bot.blank_state()
        state["recent_form_cache"][W] = {
            "checked_at": NOW, "closed": 20, "positive_rate": 1.,
            "loss_streak": 0, "net_pnl": 999, "profit_factor": 999,
        }
        rows = [{"realizedPnl": 3}] * 20
        with mock.patch.object(bot, "fetch_recent_results",
                               return_value=rows) as fetch:
            self.assertIsNone(bot.recent_form_reason(state, W, NOW))
        fetch.assert_called_once()
        self.assertEqual(state["recent_form_cache"][W]["unredeemed_lost_24h"], 0)


if __name__ == "__main__":
    unittest.main()
