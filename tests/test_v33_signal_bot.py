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
        with mock.patch.object(bot, "check_trade", return_value=(None, "book_no_liquidity")), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            n = bot.deliver(state, {W: {"name": "Tester", "winrate": .88}}, NOW)
        self.assertEqual(n, 0)
        sender.assert_not_called()
        self.assertEqual(state["counters"]["book_no_liquidity"], 1)

    def test_delivery_sends_once(self):
        state = bot.blank_state()
        bot.add_fill(state, trade(price=.5), NOW)
        meta = {W: {"name": "Tester", "winrate": .88}}
        with mock.patch.object(bot, "check_trade", return_value=(
                {"price": .51, "best_ask": .51}, None)), \
             mock.patch.object(bot.v2, "send_telegram") as sender:
            n = bot.deliver(state, meta, NOW)
            self.assertEqual(n, 1)
            sender.assert_called_once()
            bot.add_fill(state, dict(trade(price=.5), transaction_hash="different"), NOW)
            n2 = bot.deliver(state, meta, NOW)
        self.assertEqual(n2, 0)


if __name__ == "__main__":
    unittest.main()
