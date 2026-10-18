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
    def setUp(self):
        # Legacy risk-gate tests probe generic markets; the live focus is
        # separately exercised under its actual crypto_short configuration.
        focus = mock.patch.object(bot, "MARKET_FOCUS", "all")
        focus.start()
        self.addCleanup(focus.stop)

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

    def test_quarantined_wallet_never_enters_live_shortlist(self):
        risk = "0x31550df5402226b825f900a31659a05d809d268e"
        quality = {"closed": 100, "profitable_closed_rate": .90,
                   "realized_closed_pnl": 10000, "profit_factor": 5}
        data = {"schema": "v33_wallet_shortlist_1", "observed_at": NOW,
                "shortlist": [dict(quality, wallet=risk),
                              dict(quality, wallet=W)]}
        with mock.patch.object(bot, "QUARANTINE_WALLETS", frozenset({risk})):
            chosen = bot.select_wallets(data, NOW)
        self.assertNotIn(risk, chosen)
        self.assertIn(W, chosen)

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
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
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
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
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


    def test_sports_buy_precedes_priority_btc_in_sports_first_mode(self):
        sport = ("sport", {"wallet": W, "last": NOW-10,
                 "title": "Lakers vs Celtics - Moneyline",
                 "url": "https://polymarket.com/event/nba-lal-bos-2026"})
        btc_wallet = "0x" + "c"*40
        btc = ("btc", {"wallet": btc_wallet, "last": NOW,
               "title": "Bitcoin Up or Down",
               "url": "https://polymarket.com/event/btc-updown-5m-1792056900"})
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot, "PRIORITY_WALLETS", frozenset([btc_wallet])):
            # Sport outranks even a newer requested BTC trade when capped.
            self.assertEqual(sorted([btc, sport], key=bot.signal_sort_key)[0], sport)

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
        rows = [{"realizedPnl": 2}] * 10 + [{"realizedPnl": -1}] * 10
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
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            count = bot.deliver(state, {W: {"name": "Test", "winrate": .88}}, NOW)
        self.assertEqual(count, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["recent_form_request_error"], 1)
        self.assertEqual(state["counters"]["recent_history_unverified"], 1)
        self.assertEqual(len(state["pending"]), 1)  # retry while fresh, never send unknown
        self.assertEqual(state["counters"]["recent_history_retry_queued"], 1)

    def test_repeated_incomplete_history_is_cached_and_cannot_send(self):
        state=bot.blank_state()
        with mock.patch.object(bot,"fetch_recent_results",
                               side_effect=RuntimeError("history incomplete")) as req:
            self.assertEqual(bot.recent_form_reason(state,W,NOW),
                             "recent_history_unverified")
            self.assertEqual(bot.recent_form_reason(state,W,NOW+45),
                             "recent_history_unverified")
            self.assertEqual(bot.recent_form_reason(state,W,NOW+599),
                             "recent_history_unverified")
        self.assertEqual(req.call_count,1)
        self.assertEqual(state["counters"]["recent_form_request_error"],1)
        self.assertEqual(state["recent_form_cache"][W]["history_unverified"],True)


    def test_transient_recent_api_failure_retries_after_short_cooldown(self):
        state = bot.blank_state()
        good = [{"realizedPnl": 4}] * 18 + [{"realizedPnl": -2}] * 2
        with mock.patch.object(bot, "fetch_recent_results",
                               side_effect=[RuntimeError(
                                   "API network issue on /v2/positions: timed out"), good]) as fetch:
            self.assertEqual(bot.recent_form_reason(state, W, NOW),
                             "recent_history_unverified")
            self.assertEqual(bot.recent_form_reason(state, W, NOW+40),
                             "recent_history_unverified")
            self.assertIsNone(bot.recent_form_reason(state, W, NOW+61))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(state["counters"]["recent_api_transient"], 1)

    def test_unverified_recent_history_is_retried_without_alerting_unchecked(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        meta = {W: {"name": "Test", "winrate": .8}}
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot, "recent_form_reason",
                               side_effect=["recent_history_unverified", None]) as risk, \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=321) as sender:
            self.assertEqual(bot.deliver(state, meta, NOW), 0)
            self.assertEqual(len(state["pending"]), 1)
            sender.assert_not_called()
            self.assertEqual(bot.deliver(state, meta, NOW+61), 1)
        self.assertEqual(risk.call_count, 2)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(state["counters"]["recent_history_retry_queued"], 1)
        self.assertEqual(state["counters"]["alerts_sent"], 1)

    def test_unverified_history_never_promoted_after_expiry(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot, "recent_form_reason",
                               return_value="recent_history_unverified"), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            self.assertEqual(bot.deliver(state, {W: {"name": "", "winrate": .8}},
                                         NOW+bot.MAX_AGE), 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["recent_history_expired_unverified"], 1)
        self.assertEqual(state["pending"], {})

    def test_buy_not_marked_sent_without_positive_telegram_message_id(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        meta = {W: {"name": "Test", "winrate": .8}}
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram",
                               side_effect=[None, 567]) as sender:
            self.assertEqual(bot.deliver(state, meta, NOW), 0)
            self.assertEqual(len(state["pending"]), 1)
            self.assertEqual(state["counters"].get("alerts_sent", 0), 0)
            self.assertFalse(state["sent"])
            self.assertEqual(bot.deliver(state, meta, NOW+60), 1)
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(state["counters"]["telegram_error"], 1)
        self.assertEqual(state["counters"]["alerts_sent"], 1)

    def test_invalid_future_position_timestamp_exposes_numeric_diagnostic(self):
        now = 1792223000
        with mock.patch.object(bot.time, "time", return_value=now):
            with self.assertRaises(ValueError) as ctx:
                bot.position_activity_timestamp((now+900)*1000, now)
        self.assertIn("Position activity timestamp invalid", str(ctx.exception))
        self.assertIn("raw=", str(ctx.exception))
        self.assertIn("latest_allowed=", str(ctx.exception))

    def test_recent_seven_losses_stop_alert_even_with_high_old_winrate(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        rows = [{"realizedPnl": -1}] * 7 + [{"realizedPnl": 10}] * 13
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot, "fetch_recent_results", return_value=rows), \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
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
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
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

    def test_active_pagination_beyond_five_pages_can_finish_safely(self):
        pages = [
            {"data": [], "pagination": {
                "has_more": i < 6,
                "next_cursor": f"page-{i+1}" if i < 6 else None}}
            for i in range(7)
        ]
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=pages) as req:
            self.assertEqual(bot.fetch_open_positions(W), [])
        self.assertEqual(req.call_count, 7)

    def test_active_pagination_still_fails_closed_at_new_safety_cap(self):
        pages = [
            {"data": [], "pagination": {
                "has_more": True, "next_cursor": f"page-{i+1}"}}
            for i in range(bot.ACTIVE_PAGE_CAP)
        ]
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=pages) as req:
            with self.assertRaisesRegex(RuntimeError,
                                        "incomplete wallet snapshot"):
                bot.fetch_open_positions(W)
        self.assertEqual(req.call_count, bot.ACTIVE_PAGE_CAP)

    def test_active_pagination_rejects_cursor_cycle(self):
        pages = [
            {"data": [], "pagination": {
                "has_more": True, "next_cursor": "A"}},
            {"data": [], "pagination": {
                "has_more": True, "next_cursor": "B"}},
            {"data": [], "pagination": {
                "has_more": True, "next_cursor": "A"}}]
        with mock.patch.object(bot.v2, "request_json", side_effect=pages):
            with self.assertRaisesRegex(ValueError, "cursor invalid"):
                bot.fetch_open_positions(W)

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
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
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
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
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
        # This fixture tests merging, not early-stop of a clearly bad wallet.
        with mock.patch.object(bot, "RECENT_MAX_UNREDEEMED_LOSSES_24H", 99), \
             mock.patch.object(bot.v2, "request_json",
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

    def test_major_losses_older_than_24h_still_block_ufc_wallet(self):
        now = NOW
        # Wallet can score 20/20 on profitable CLOSED positions while
        # retaining many zero-payout sports tokens with -$600 each.
        rows = [{"realizedPnl": 15, "timestamp": now-i}
                for i in range(20)]
        rows += [{"realizedPnl": -600, "_unredeemed_lost": True,
                  "timestamp": now - 7*86400 - i}
                 for i in range(5)]
        state = bot.blank_state()
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            reason = bot.recent_form_reason(state, W, now)
        self.assertEqual(reason, "major_unredeemed_loss_cluster_90d")
        self.assertEqual(state["recent_form_cache"][W]["unredeemed_lost_24h"], 0)
        self.assertEqual(state["recent_form_cache"][W]["major_unredeemed_lost_90d"], 5)
        self.assertEqual(state["recent_form_cache"][W]["positive_rate"], 1)

    def test_lost_positions_fetch_explicitly_includes_archived(self):
        now = NOW
        replies = [
            {"data": [], "pagination": {"has_more": False}},
            {"data": [], "pagination": {"has_more": False}}
        ]
        with mock.patch.object(bot.v2, "request_json",
                               side_effect=replies) as req:
            bot.fetch_recent_results(W, now)
        self.assertNotIn("include_archived", req.call_args_list[0].args[1])
        self.assertEqual(req.call_args_list[1].args[1]["include_archived"], "true")

    def test_settled_losses_follow_all_archived_pages(self):
        now = NOW
        def lost(token):
            return {"proxy_wallet": W, "condition_id": COND,
                    "token_id": str(token), "last_event_at": now-86400,
                    "status": "REDEEMABLE", "redeemable": True,
                    "realized_pnl": 0, "unrealized_pnl": -600}
        replies = [
            {"data": [], "pagination": {"has_more": False}},
            {"data": [lost(1)], "pagination":
                {"has_more": True, "next_cursor": "page2"}},
            {"data": [lost(2)], "pagination": {"has_more": False}}
        ]
        with mock.patch.object(bot.v2, "request_json", side_effect=replies) as req:
            rows = bot.fetch_recent_results(W, now)
        self.assertEqual(len(rows), 2)
        self.assertEqual(req.call_count, 3)
        self.assertEqual(req.call_args_list[-1].args[1]["cursor"], "page2")

    def test_incomplete_settled_loss_inventory_fails_closed(self):
        now = NOW
        pages = [
            {"data": [], "pagination": {"has_more": False}}
        ] + [
            {"data": [], "pagination":
                {"has_more": True, "next_cursor": f"next_{i}"}}
            for i in range(4)
        ]
        with mock.patch.object(bot, "RECENT_LOSS_PAGE_CAP", 4), \
             mock.patch.object(bot.v2, "request_json", side_effect=pages):
            with self.assertRaisesRegex(RuntimeError, "history incomplete"):
                bot.fetch_recent_results(W, now)

    def test_many_unredeemed_major_losses_veto_early_and_cache(self):
        # Four confirmed major losses on first page are decisive, even if
        # the API has another 2,000 settled losers awaiting pagination.
        rows=[{"proxy_wallet":W,"condition_id":COND,
               "token_id":str(i),"last_event_at":NOW-100,
               "status":"REDEEMABLE","redeemable":True,
               "realized_pnl":0,"unrealized_pnl":-125}
              for i in range(5)]
        pages=[{"data":[],"pagination":{"has_more":False}},
               {"data":rows,"pagination":{"has_more":True,
                                          "next_cursor":"page2"}}]
        state=bot.blank_state()
        with mock.patch.object(bot.v2,"request_json",
                               side_effect=pages) as req:
            self.assertEqual(bot.recent_form_reason(state,W,NOW),
                             "major_unredeemed_loss_cluster_90d")
        self.assertEqual(req.call_count,2)
        with mock.patch.object(bot,"fetch_recent_results",
                               side_effect=AssertionError("must cache")):
            self.assertEqual(bot.recent_form_reason(state,W,NOW+250),
                             "major_unredeemed_loss_cluster_90d")
        self.assertEqual(state["recent_form_cache"][W]["confirmed_risk_veto"],
                         "major_unredeemed_loss_cluster_90d")

    def test_zero_activity_date_keeps_settled_loser_but_does_not_certify_recent(self):
        winners = [
            {"proxy_wallet": W, "condition_id": COND, "token_id": str(100+i),
             "last_event_at": NOW-i, "status": "CLOSED", "realized_pnl": 8}
            for i in range(20)]
        undated = {
            "proxy_wallet": W, "condition_id": COND, "token_id": "zero-loss",
            "last_event_at": 0, "status": "REDEEMABLE", "redeemable": True,
            "realized_pnl": 0, "unrealized_pnl": -35}
        replies = [
            {"data": winners, "pagination": {"has_more": False}},
            {"data": [undated], "pagination": {"has_more": False}},
        ]
        with mock.patch.object(bot.v2, "request_json", side_effect=replies):
            rows = bot.fetch_recent_results(W, NOW)
        self.assertEqual(len(rows), 21)
        self.assertIsNone(rows[-1]["timestamp"])
        self.assertTrue(rows[-1]["_timestamp_unknown"])
        form = bot.recent_form(rows, NOW)
        self.assertEqual(form["undated_loss_positions"], 1)
        self.assertEqual(form["closed"], 20)
        self.assertEqual(form["unredeemed_lost_24h"], 0)
        self.assertEqual(form["major_unredeemed_lost_90d"], 0)

    def test_undated_loser_blocks_live_qualification_without_api_error(self):
        good = [{"realizedPnl": 10, "timestamp": NOW-i}
                for i in range(20)]
        undated = {"realizedPnl": -40, "timestamp": None,
                   "_unredeemed_lost": True, "_timestamp_unknown": True}
        state = bot.blank_state()
        with mock.patch.object(bot, "fetch_recent_results",
                               return_value=good + [undated]) as fetch:
            self.assertEqual(bot.recent_form_reason(state, W, NOW),
                             "recent_undated_loss_history")
            self.assertEqual(bot.recent_form_reason(state, W, NOW+180),
                             "recent_undated_loss_history")
        fetch.assert_called_once()
        self.assertEqual(state["counters"].get("recent_form_request_error", 0), 0)
        self.assertEqual(state["recent_form_cache"][W]["undated_loss_positions"], 1)

    def test_closed_position_with_zero_activity_date_is_not_silently_accepted(self):
        broken = {"proxy_wallet": W, "condition_id": COND,
                  "token_id": "closed-zero", "last_event_at": 0,
                  "status": "CLOSED", "realized_pnl": 10}
        with mock.patch.object(bot.v2, "request_json",
                               return_value={"data": [broken],
                                             "pagination": {"has_more": False}}):
            with self.assertRaisesRegex(ValueError, "timestamp invalid"):
                bot.fetch_recent_results(W, NOW)

    def test_valid_timestamp_milliseconds_and_delayed_poll_second_clock(self):
        at=1792223000  # actual epoch seconds, not the synthetic 1970-era NOW
        with mock.patch.object(bot.time,"time",return_value=at+140):
            self.assertEqual(bot.position_activity_timestamp(
                (at+100)*1000,at),at+100)
            self.assertEqual(bot.position_activity_timestamp(
                at+100,at),at+100)
            with self.assertRaisesRegex(ValueError,"invalid"):
                bot.position_activity_timestamp((at+900)*1000,at)
        with self.assertRaises(ValueError):
            bot.position_activity_timestamp(None,at)

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


    def test_material_loss_gate_does_not_count_dust(self):
        rows = [{"realizedPnl": 2., "timestamp": NOW-i} for i in range(20)]
        rows += [{"realizedPnl": -.01, "timestamp": NOW-50-i,
                  "_unredeemed_lost": True} for i in range(7)]
        self.assertEqual(bot.recent_form(rows, NOW)["unredeemed_lost_24h"], 0)
        rows.append({"realizedPnl": -6., "timestamp": NOW-70,
                     "_unredeemed_lost": True})
        self.assertEqual(bot.recent_form(rows, NOW)["unredeemed_lost_24h"], 1)


    def test_recent_v2_skips_breakeven_redeemables_without_hiding_real_losses(self):
        base = {"proxy_wallet": W, "condition_id": COND,
                "last_event_at": NOW, "status": "REDEEMABLE",
                "redeemable": True, "realized_pnl": 0}
        unrelated = dict(base, token_id="winner", unrealized_pnl=0)
        real_loss = dict(base, token_id="loser", unrealized_pnl=-15)
        replies = [{"data": [], "pagination": {"has_more": False}},
                   {"data": [unrelated, real_loss],
                    "pagination": {"has_more": False}}]
        with mock.patch.object(bot.v2, "request_json", side_effect=replies):
            result = bot.fetch_recent_results(W, NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["token_id"], "loser")
        self.assertEqual(result[0]["realizedPnl"], -15)



    def test_quiet_status_sends_only_one_truthful_update_per_hour(self):
        state = bot.blank_state()
        state["sent"][W + "|" + COND + "|" + TOKEN] = NOW-7200
        with mock.patch.object(bot.v2, "send_telegram") as send:
            self.assertTrue(bot.send_quiet_status(state, NOW, 23, 250))
            self.assertFalse(bot.send_quiet_status(state, NOW+120, 22, 250))
            self.assertTrue(bot.send_quiet_status(state, NOW+3601, 10, 250))
        self.assertEqual(send.call_count, 2)
        self.assertIn("Это статус, не сигнал к трейду.", send.call_args.args[0])
        self.assertEqual(state["counters"]["quiet_status_sent"], 2)
        self.assertNotIn("alerts_sent", state["counters"])

    def test_quiet_status_silent_when_recent_verified_trade(self):
        state = bot.blank_state()
        state["sent"][W + "|" + COND + "|" + TOKEN] = NOW-300
        with mock.patch.object(bot.v2, "send_telegram") as send:
            self.assertFalse(bot.send_quiet_status(state, NOW, 0, 250))
        send.assert_not_called()



    def test_same_market_fresh_second_buy_allowed_after_30_minutes(self):
        state = bot.blank_state()
        base = {W: {"name": "Trader", "winrate": .82}}
        def quote(*args, **kwargs):
            return {"price": .6, "best_ask": .6, "spread": .02}, None
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", side_effect=quote), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            self.assertTrue(bot.add_fill(state, trade(), NOW))
            self.assertEqual(bot.deliver(state, base, NOW), 1)
            newer = dict(trade(timestamp=NOW + 29*60), transaction_hash="newer")
            self.assertTrue(bot.add_fill(state, newer, NOW+29*60))
            self.assertEqual(bot.deliver(state, base, NOW+29*60), 0)
            late = dict(trade(timestamp=NOW + 31*60), transaction_hash="late")
            self.assertTrue(bot.add_fill(state, late, NOW+31*60))
            self.assertEqual(bot.deliver(state, base, NOW+31*60), 1)
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(state["counters"]["alerts_sent"], 2)

    def test_delayed_verification_must_not_alert_on_expired_5m_market(self):
        start = 1792056900
        now = start + 185
        row = trade(timestamp=now)
        row["event_slug"] = "btc-updown-5m-1792056900"
        state = bot.blank_state()
        self.assertTrue(bot.add_fill(state, row, now))
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=({"price": .6}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            counter = iter([now, now+35])
            result = bot.deliver(state, {W: {"name": "", "winrate": .78}},
                                 now, clock=lambda: next(counter))
        self.assertEqual(result, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["fast_market_too_late"], 1)

    def test_better_recent_form_requires_positive_pnl_and_profit_factor(self):
        # A mere 55% positive positions cannot override losing unit economics.
        rows = [{"realizedPnl": 1}] * 13 + [{"realizedPnl": -10}] * 7
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            self.assertEqual(bot.recent_form_reason(bot.blank_state(), W, NOW),
                             "recent_negative_expectancy_sample")


    def test_opposing_wallets_trigger_deduped_explicit_conflict_warning(self):
        rival = "0x" + "c"*40
        first = trade()
        second = dict(trade(outcome="No"), proxy_wallet=rival,
                      token_id="5678", transaction_hash="rival-trade")
        state = bot.blank_state()
        self.assertTrue(bot.add_fill(state, first, NOW))
        self.assertTrue(bot.add_fill(state, second, NOW))
        chosen = {W: {"name": "A", "winrate": .8},
                  rival: {"name": "B", "winrate": .8}}
        with mock.patch.object(bot, "political_market_reason",
                               return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            self.assertEqual(bot.notify_opposing_wallets(state, chosen, NOW), 1)
            self.assertEqual(bot.notify_opposing_wallets(state, chosen, NOW+30), 0)
        self.assertEqual(sender.call_count, 1)
        warning = sender.call_args.args[0]
        self.assertIn("КОНФЛИКТ", warning)
        self.assertIn("НЕ подтверждены", warning)
        self.assertIn("\n", warning)
        self.assertEqual(state["counters"]["conflict_alerts_sent"], 1)
        self.assertNotIn("alerts_sent", state["counters"])

    def test_same_outcome_different_wallets_do_not_raise_conflict(self):
        rival = "0x" + "c"*40
        state = bot.blank_state()
        bot.add_fill(state, trade(), NOW)
        bot.add_fill(state, dict(trade(), proxy_wallet=rival,
                                 transaction_hash="other-hash"), NOW)
        selected = {W: {}, rival: {}}
        with mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            self.assertEqual(bot.notify_opposing_wallets(
                state, selected, NOW), 0)
        sender.assert_not_called()

    def test_political_conflict_is_not_announced(self):
        rival = "0x" + "c"*40
        state = bot.blank_state()
        bot.add_fill(state, trade(), NOW)
        bot.add_fill(state, dict(trade(outcome="No"), proxy_wallet=rival,
                                 token_id="other", transaction_hash="different"), NOW)
        with mock.patch.object(bot, "political_market_reason",
                               return_value="political_market"), \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            self.assertEqual(bot.notify_opposing_wallets(
                state, {W: {}, rival: {}}, NOW), 0)
        sender.assert_not_called()



    def test_consecutive_small_buys_sum_to_one_qualified_signal(self):
        state = bot.blank_state()
        first = trade(price=.5, timestamp=NOW)
        first["size"] = 40  # $20
        second = trade(price=.5, timestamp=NOW+40)
        second.update(size=44, transaction_hash="new-buy")  # $22
        chosen = {W: {"name": "", "winrate": .8}}
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade",
                               return_value=({"price": .5}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            self.assertTrue(bot.add_fill(state, first, NOW))
            self.assertEqual(bot.deliver(state, chosen, NOW), 0)
            self.assertEqual(len(state["pending"]), 1)
            self.assertTrue(bot.add_fill(state, second, NOW+40))
            self.assertEqual(bot.deliver(state, chosen, NOW+40), 1)
        self.assertEqual(sender.call_count, 1)
        self.assertAlmostEqual(state["counters"]["alerts_sent"], 1)
        sender.assert_called_once()
        self.assertIn("$42", sender.call_args.args[0])

    def test_old_subthreshold_fills_expire_and_never_combine_late(self):
        state = bot.blank_state()
        first = trade(price=.5, timestamp=NOW)
        first["size"] = 40  # $20
        later = trade(price=.5, timestamp=NOW+240)
        later.update(size=40, transaction_hash="after-window")
        bot.add_fill(state, first, NOW)
        self.assertEqual(bot.deliver(
            state, {W: {"name": "", "winrate": .8}}, NOW), 0)
        bot.add_fill(state, later, NOW+240)
        group = next(iter(state["pending"].values()))
        self.assertEqual(group["first"], NOW+240)
        self.assertEqual(len(state["seen"]), 2)
        cand, why = bot.candidate(group, NOW+240)
        self.assertEqual(why, "small_trade")
        self.assertIsNone(cand)



    def test_telegram_api_ack_requires_message_id_and_matching_numeric_chat(self):
        import io
        import json
        def reply(payload):
            return io.BytesIO(json.dumps(payload).encode("utf-8"))
        credentials = {"TELEGRAM_BOT_TOKEN": "dummy-valid-token",
                       "TELEGRAM_CHAT_ID": "12345678"}
        ok = {"ok": True, "result": {
            "message_id": 42, "chat": {"id": 12345678}}}
        wrong = {"ok": True, "result": {
            "message_id": 43, "chat": {"id": 999}}}
        missing = {"ok": True, "result": {
            "chat": {"id": 12345678}}}
        with mock.patch.dict(bot.os.environ, credentials), \
             mock.patch.object(bot.v2.urllib.request, "urlopen",
                               return_value=reply(ok)) as post:
            self.assertEqual(bot.v2.send_telegram("test"), 42)
            post.assert_called_once()
        for response in [wrong, missing]:
            with mock.patch.dict(bot.os.environ, credentials), \
                 mock.patch.object(bot.v2.urllib.request, "urlopen",
                                   return_value=reply(response)):
                with self.assertRaises(RuntimeError):
                    bot.v2.send_telegram("test")

    def test_v33_persists_safe_telegram_acceptance_id(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        with mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade",
                               return_value=({"price": .5}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=444):
            count = bot.deliver(state, {
                W: {"name": "Test", "winrate": .82}}, NOW)
        self.assertEqual(count, 1)
        self.assertEqual(state["last_telegram_acceptance"]["message_id"], 444)
        self.assertIn("not verified", state["last_telegram_acceptance"]["note"])
        self.assertNotIn("chat_id", state["last_telegram_acceptance"])



    def test_hourly_heartbeat_records_telegram_ack_separately_from_buy(self):
        state = bot.blank_state()
        state["sent"][W + "|" + COND + "|" + TOKEN] = NOW-4000
        with mock.patch.object(bot.v2, "send_telegram", return_value=991) as sender:
            self.assertTrue(bot.send_quiet_status(state, NOW, 0, 250))
            sender.assert_called_once()
        ack = state["last_telegram_acceptance"]
        self.assertEqual(ack["kind"], "status")
        self.assertEqual(ack["message_id"], 991)
        self.assertIn("client push/display is not verified", ack["note"])
        self.assertNotIn("alerts_sent", state["counters"])



    def test_main_two_real_polls_persist_partial_buy_then_alert(self):
        """Full pipeline: poll -> checkpoint -> next poll -> real BUY route."""
        import json
        import tempfile
        from pathlib import Path
        wallet_meta = {"wallet": W, "closed": 100,
                       "profitable_closed_rate": .81,
                       "realized_closed_pnl": 1200,
                       "profit_factor": 2.2, "name": ""}
        a = trade(price=.5, timestamp=NOW)
        a["size"] = 40  # $20
        b = trade(price=.5, timestamp=NOW+45)
        b.update(size=44, transaction_hash="second-fill")
        with tempfile.TemporaryDirectory() as d:
            shortlist = Path(d) / "shortlist.json"
            state_file = Path(d) / "state.json"
            shortlist.write_text(json.dumps({
                "schema": "v33_wallet_shortlist_1", "observed_at": NOW,
                "candidate_count": 1, "shortlist": [wallet_meta]}))
            env = {"TELEGRAM_V31_BOT_TOKEN": "old1",
                   "TELEGRAM_V32_BOT_TOKEN": "old2",
                   "TELEGRAM_V33_BOT_TOKEN": "new3",
                   "TELEGRAM_CHAT_ID": "12345678"}
            with mock.patch.object(bot, "SHORTLIST", shortlist), \
                 mock.patch.object(bot, "STATE", state_file), \
                 mock.patch.dict(bot.os.environ, env), \
                 mock.patch.object(bot.v32, "get_wallet_trades",
                                   side_effect=[[a], [b]]) as fetch, \
                 mock.patch.object(bot, "political_market_reason",
                                   return_value=None), \
                 mock.patch.object(bot, "check_trade",
                                   return_value=({"price": .51}, None)), \
                 mock.patch.object(bot, "recent_form_reason",
                                   return_value=None), \
                 mock.patch.object(bot, "active_form_reason",
                                   return_value=None), \
                 mock.patch.object(bot, "notify_opposing_wallets",
                                   side_effect=AssertionError("conflict Telegram must be muted")) as conflict_notifier, \
                 mock.patch.object(bot.v2, "send_telegram",
                                   return_value=4321) as send:
                with mock.patch.object(bot.time, "time", return_value=NOW):
                    bot.main()
                checkpoint = json.loads(state_file.read_text())
                self.assertEqual(checkpoint["counters"].get("alerts_sent", 0), 0)
                self.assertEqual(len(checkpoint["pending"]), 1)
                with mock.patch.object(bot.time, "time",
                                       return_value=NOW+45):
                    bot.main()
                saved = json.loads(state_file.read_text())
        self.assertEqual(fetch.call_count, 2)
        conflict_notifier.assert_not_called()
        self.assertEqual(send.call_count, 2)  # one boot + one genuine BUY
        self.assertEqual(saved["counters"]["alerts_sent"], 1)
        self.assertEqual(saved["last_telegram_acceptance"]["message_id"], 4321)
        self.assertEqual(saved["last_telegram_acceptance"]["kind"], "buy")
        self.assertEqual(saved["pending"], {})
        self.assertEqual(saved["last_run"]["alerts"], 1)



    def test_live_crypto_focus_blocks_spiderman_before_api_or_telegram(self):
        state = bot.blank_state()
        row = trade(price=.5)
        row["title"] = "Will Spider-Man 5 release in 2029?"
        row["event_slug"] = "will-spiderman-5-release-in-2029"
        self.assertTrue(bot.add_fill(state, row, NOW))
        group = next(iter(state["pending"].values()))
        with mock.patch.object(bot, "MARKET_FOCUS", "crypto_short"), \
             mock.patch.object(bot.v2, "request_json") as api, \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            self.assertEqual(bot.market_focus_reason(state, group, NOW),
                             "off_topic_market")
            self.assertEqual(bot.deliver(state, {W: {"name": "", "winrate": .8}},
                                         NOW), 0)
        api.assert_not_called()
        sender.assert_not_called()
        self.assertEqual(state["counters"]["off_topic_market"], 1)

    def test_live_crypto_focus_admits_fresh_btc_5m_and_15m(self):
        from unittest import mock
        start = 1792056900
        for period in ("5m", "15m"):
            row = trade(price=.5, timestamp=start+30)
            row["title"] = "Bitcoin Up or Down"
            row["event_slug"] = f"btc-updown-{period}-{start}"
            state = bot.blank_state()
            self.assertTrue(bot.add_fill(state, row, start+30))
            group = next(iter(state["pending"].values()))
            with mock.patch.object(bot, "MARKET_FOCUS", "crypto_short"), \
                 mock.patch.object(bot.v2, "request_json") as api:
                self.assertIsNone(bot.market_focus_reason(state, group,
                                                           start+30))
                self.assertEqual(bot.market_focus_reason(
                    state, group, start+(300 if period == "5m" else 900)-40),
                    "crypto_market_too_late")
            api.assert_not_called()

    def test_live_crypto_focus_excludes_far_future_bitcoin_market(self):
        import datetime
        now = 1792137600
        state = bot.blank_state()
        row = trade(price=.5, timestamp=now)
        row["title"] = "Will Bitcoin hit $300,000 by 2029?"
        self.assertTrue(bot.add_fill(state, row, now))
        group = next(iter(state["pending"].values()))
        response = [{"conditionId": COND,
                     "endDate": datetime.datetime.fromtimestamp(
                         now+365*86400, datetime.timezone.utc).isoformat()}]
        with mock.patch.object(bot, "MARKET_FOCUS", "crypto_short"), \
             mock.patch.object(bot.v2, "request_json", return_value=response):
            self.assertEqual(bot.market_focus_reason(state, group, now),
                             "crypto_market_too_far")

    def test_live_crypto_focus_allows_verified_intraday_btc(self):
        import datetime
        now = 1792137600
        state = bot.blank_state()
        row = trade(price=.5, timestamp=now)
        row["title"] = "Will Bitcoin be above $69k today?"
        self.assertTrue(bot.add_fill(state, row, now))
        group = next(iter(state["pending"].values()))
        response = [{"conditionId": COND,
                     "endDate": datetime.datetime.fromtimestamp(
                         now+3600, datetime.timezone.utc).isoformat()}]
        with mock.patch.object(bot, "MARKET_FOCUS", "crypto_short"), \
             mock.patch.object(bot.v2, "request_json", return_value=response) as api:
            self.assertIsNone(bot.market_focus_reason(state, group, now))
            self.assertIsNone(bot.market_focus_reason(state, group, now+200))
        api.assert_called_once()  # cached Gamma market horizon

    def test_live_crypto_focus_fails_closed_if_market_end_missing(self):
        now = 1792137600
        state = bot.blank_state()
        row = trade(price=.5, timestamp=now)
        row["title"] = "Will Ethereum exceed $5,000 today?"
        bot.add_fill(state, row, now)
        group = next(iter(state["pending"].values()))
        with mock.patch.object(bot, "MARKET_FOCUS", "crypto_short"), \
             mock.patch.object(bot.v2, "request_json",
                               return_value=[{"conditionId": COND}]):
            self.assertEqual(bot.market_focus_reason(state, group, now),
                             "market_end_unverified")

    def test_live_crypto_focus_btc_5m_full_delivery(self):
        start = 1792056900
        now = start+30
        state = bot.blank_state()
        row = trade(price=.5, timestamp=now)
        row.update(title="Bitcoin Up or Down", event_slug=f"btc-updown-5m-{start}")
        bot.add_fill(state, row, now)
        with mock.patch.object(bot, "MARKET_FOCUS", "crypto_short"), \
             mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade",
                               return_value=({"price": .5}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=1234):
            self.assertEqual(bot.deliver(state, {
                W: {"name": "", "winrate": .8}}, now), 1)
        self.assertEqual(state["counters"]["alerts_sent"], 1)



    def test_forward_evidence_survives_current_poll_clock_advance(self):
        # This reproduced the data-loss bug: main() recorded now before
        # long API checks, then quotes were written a few seconds later.
        start=1792220000
        signal={"wallet":W,"alert_at":start+7}
        shadow={"wallet":W,"quoted_at":start+12}
        self.assertEqual(bot.keep_forward_evidence(
            {"new":signal},start,"alert_at"),{"new":signal})
        self.assertEqual(bot.keep_forward_evidence(
            {"new":shadow},start,"quoted_at"),{"new":shadow})
        # No unbounded future timestamp or 45-day-old quote is trusted.
        self.assertEqual(bot.keep_forward_evidence(
            {"future":{"alert_at":start+901},
             "stale":{"alert_at":start-46*86400}},
            start,"alert_at"),{})

    def test_shadow_cap_preserves_current_poll_quote_after_clock_skew(self):
        start=1792220000
        values={str(i):{"quoted_at":start+i} for i in range(12)}
        kept=bot.keep_forward_evidence(
            values,start,"quoted_at",limit=5)
        self.assertEqual(list(kept.keys()),["11","10","9","8","7"])

    def test_book_verified_sports_shadow_is_not_a_buy_or_telegram(self):
        now = 1792137600
        state = bot.blank_state()
        row = trade(price=.5, timestamp=now)
        row.update(title="Lakers vs Celtics - Moneyline",
                   event_slug="nba-lal-bos-2026")
        bot.add_fill(state, row, now)
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot, "market_focus_reason", return_value=None), \
             mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade",
                 return_value=({"price":.51, "best_ask":.51},None)), \
             mock.patch.object(bot, "recent_form_reason",
                 return_value="recent_positive_rate_low"), \
             mock.patch.object(bot.v2, "send_telegram") as telegram:
            result=bot.deliver(state,{W:{"name":"","winrate":.80}},now)
        telegram.assert_not_called()
        self.assertEqual(result,0)
        self.assertEqual(state["counters"].get("alerts_sent",0),0)
        quote=next(iter(state["shadow_evidence"].values()))
        self.assertEqual(quote["decision"],"blocked_recent")
        self.assertEqual(quote["topic"],"sports")
        self.assertAlmostEqual(quote["hypothetical_copy_ask"],.51)
        self.assertNotIn("signal_evidence",quote)

    def test_sports_funnel_records_candidates_without_fake_buy(self):
        state = bot.blank_state()
        now = 1792137600
        row = trade(price=.5, timestamp=now)
        row.update(title="Lakers vs Celtics - Moneyline",
                   event_slug="nba-lal-bos-2026")
        bot.add_fill(state, row, now)
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot, "market_focus_reason", return_value=None), \
             mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot, "check_trade", return_value=(
                 None, "book_no_liquidity")), \
             mock.patch.object(bot.v2, "send_telegram", return_value=123) as sender:
            self.assertEqual(bot.deliver(
                state, {W: {"name": "", "winrate": .8}}, now), 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["eligible_source_buy_sports"], 1)
        self.assertEqual(state["counters"]["book_no_liquidity"], 1)
        self.assertNotIn("book_verified_sports", state["counters"])
        self.assertNotIn("alerts_sports", state["counters"])

    def test_sports_btc_mode_accepts_official_nba_game(self):
        import datetime
        now = 1792137600
        state = bot.blank_state()
        row = trade(price=.5, timestamp=now)
        row.update(title="Lakers vs Celtics - Moneyline",
                   event_slug="nba-lal-bos-2026")
        bot.add_fill(state, row, now)
        group = next(iter(state["pending"].values()))
        game = datetime.datetime.fromtimestamp(
            now+3600, datetime.timezone.utc).isoformat()
        match = {"conditionId": COND, "sportsMarketType": "moneyline",
                 "gameStartTime": game, "endDate": game}
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot.v2, "request_json",
                               return_value=[match]) as api:
            self.assertIsNone(bot.market_focus_reason(state, group, now))
            self.assertIsNone(bot.market_focus_reason(state, group, now+180))
        api.assert_called_once()

    def test_sports_and_btc_buy_counters_are_only_incremented_after_send(self):
        import datetime
        now = 1792137600
        sports = bot.blank_state()
        row = trade(price=.5, timestamp=now)
        row.update(title="Lakers vs Celtics - Moneyline",
                   event_slug="nba-lal-bos-2026")
        bot.add_fill(sports, row, now)
        kickoff = datetime.datetime.fromtimestamp(
            now+3600, datetime.timezone.utc).isoformat()
        market = {"conditionId": COND, "sportsMarketType": "moneyline",
                  "gameStartTime": kickoff}
        mocks = {
            "check_trade": ({"price": .51, "best_ask": .51}, None),
            "recent_form_reason": None,
            "active_form_reason": None,
            "political_market_reason": None,
        }
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot.v2, "request_json", return_value=[market]), \
             mock.patch.object(bot, "check_trade", return_value=mocks["check_trade"]), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=987):
            self.assertEqual(bot.deliver(sports, {
                W: {"name": "", "winrate": .8}}, now), 1)
        self.assertEqual(sports["counters"]["alerts_sent"], 1)
        self.assertEqual(sports["shadow_evidence"][W+"|"+COND+"|"+TOKEN]["decision"],
                         "sent_telegram")
        evidence = sports["signal_evidence"][W+"|"+COND+"|"+TOKEN]
        self.assertAlmostEqual(evidence["hypothetical_copy_ask"], .51)
        self.assertEqual(evidence["hypothetical_copy_stake_usd"], bot.QUOTE_STAKE)
        self.assertIn("NOT a filled order", evidence["note"])
        self.assertEqual(sports["counters"]["alerts_sports"], 1)
        self.assertNotIn("alerts_btc", sports["counters"])

        start = 1792056900
        when = start + 30
        btc = bot.blank_state()
        btc_row = trade(price=.5, timestamp=when)
        btc_row.update(title="Bitcoin Up or Down",
                       event_slug=f"btc-updown-5m-{start}")
        bot.add_fill(btc, btc_row, when)
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot, "check_trade", return_value=(
                 {"price": .5, "best_ask": .5}, None)), \
             mock.patch.object(bot, "recent_form_reason", return_value=None), \
             mock.patch.object(bot, "active_form_reason", return_value=None), \
             mock.patch.object(bot, "political_market_reason", return_value=None), \
             mock.patch.object(bot.v2, "send_telegram", return_value=988):
            self.assertEqual(bot.deliver(btc, {
                W: {"name": "", "winrate": .8}}, when), 1)
        self.assertEqual(btc["counters"]["alerts_btc"], 1)
        self.assertNotIn("alerts_sports", btc["counters"])

    def test_sports_btc_mode_allows_inplay_even_if_enddate_was_kickoff(self):
        import datetime
        now = 1792137600
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5, timestamp=now), now)
        group = next(iter(state["pending"].values()))
        kickoff = datetime.datetime.fromtimestamp(
            now-3600, datetime.timezone.utc).isoformat()
        match = {"conditionId": COND, "sportsMarketType": "moneyline",
                 "gameStartTime": kickoff, "endDate": kickoff}
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot.v2, "request_json", return_value=[match]):
            self.assertIsNone(bot.market_focus_reason(state, group, now))

    def test_sports_btc_rejects_movies_and_altcoins(self):
        now = 1792137600
        for title in ["Will Spider-Man release in 2028?",
                      "Ethereum Up or Down 5m", "Will Solana hit $300?"]:
            state = bot.blank_state()
            row = trade(price=.5, timestamp=now)
            row["title"] = title
            bot.add_fill(state, row, now)
            with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
                 mock.patch.object(bot.v2, "request_json",
                                   return_value=[{"conditionId": COND}]):
                self.assertEqual(bot.market_focus_reason(
                    state, next(iter(state["pending"].values())), now),
                    "off_topic_market")

    def test_sports_btc_rejects_unverified_pop_culture_as_sport(self):
        now = 1792137600
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5, timestamp=now), now)
        with mock.patch.object(bot, "MARKET_FOCUS", "sports_btc"), \
             mock.patch.object(bot.v2, "request_json",
                               return_value=[{"conditionId": COND,
                                              "category": "Entertainment"}]):
            self.assertEqual(bot.market_focus_reason(
                state, next(iter(state["pending"].values())), now),
                "off_topic_market")

    def test_repeated_recent_losses_block_high_old_winrate(self):
        rows = [{"realizedPnl": -5}] * 2 + [{"realizedPnl": 9}] * 18
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            self.assertEqual(bot.recent_form_reason(
                bot.blank_state(), W, NOW), "recent_loss_streak")



    def test_last_five_mostly_losses_block_high_old_winrate(self):
        rows = [{"realizedPnl": 1}, {"realizedPnl": -4},
                {"realizedPnl": 1}, {"realizedPnl": -4},
                {"realizedPnl": -4}] + [{"realizedPnl": 10}] * 15
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            state = bot.blank_state()
            self.assertEqual(bot.recent_form_reason(state, W, NOW),
                             "recent_last5_poor")
            self.assertEqual(state["recent_form_cache"][W]["last5_wins"], 2)
            self.assertLess(state["recent_form_cache"][W]["last5_net_pnl"], 0)

    def test_last_five_winning_pnl_passes_even_with_one_last_loss(self):
        rows = [{"realizedPnl": -1}, {"realizedPnl": 6},
                {"realizedPnl": 6}, {"realizedPnl": 6},
                {"realizedPnl": -2}] + [{"realizedPnl": 5}] * 15
        with mock.patch.object(bot, "fetch_recent_results", return_value=rows):
            self.assertIsNone(bot.recent_form_reason(bot.blank_state(), W, NOW))


    def test_conflict_risk_veto_survives_muting_telegram_notices(self):
        rival = "0x" + "c" * 40
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        bot.add_fill(state, dict(trade(price=.5, outcome="No"),
                                 proxy_wallet=rival, token_id="other-token",
                                 transaction_hash="distinct-rival"), NOW)
        group = next(x for x in state["pending"].values()
                     if x["wallet"] == W)
        cand, reason = bot.candidate(group, NOW)
        self.assertIsNone(reason)
        with mock.patch.object(bot, "CONFLICT_TELEGRAM_ENABLED", False), \
             mock.patch.object(bot.v2, "send_telegram") as send:
            self.assertTrue(bot.conflicting_wallet_signal(
                state, group, cand, NOW))
        send.assert_not_called()
        self.assertIn(COND, state["conflicting_markets"])


if __name__ == "__main__":
    unittest.main()
