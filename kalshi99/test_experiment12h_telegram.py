"""Offline end-to-end safety tests for Kalshi 12h Telegram notifications."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from kalshi99 import experiment12h as ex
from kalshi99 import experiment12h_telegram as notify

VALID = {
    "TELEGRAM_V31_BOT_TOKEN": "previous-bot-test-only",
    "TELEGRAM_V32_BOT_TOKEN": "new-bot-test-only",
    "TELEGRAM_CHAT_ID": "123456789",
}


class TwelveHourTelegramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.state = str(d / "experiment.json")
        self.snapshot = str(d / "snapshots.jsonl")
        self.ack = str(d / "sent.json")
        self.portfolio = ex.blank_state()
        self.portfolio["scans"] = 1
        self.portfolio["total_verified_quotes"] = 57
        self.portfolio["unique_verified_market_sides"] = ["T-1:yes"]
        Path(self.state).write_text(json.dumps(self.portfolio))

    def test_first_message_and_checkpoint_no_duplicate(self):
        send = Mock()
        now = ex.START + timedelta(minutes=7)
        self.assertEqual(notify.run(self.state, self.snapshot, self.ack, now, VALID, send), 1)
        self.assertIn("ПОДКЛЮЧЕНО", send.call_args.args[2])
        self.assertIn("57", send.call_args.args[2])
        self.assertEqual(notify.run(self.state, self.snapshot, self.ack, now, VALID, send), 0)
        self.assertEqual(send.call_count, 1)

    def test_invalid_token_blocks_all_messages(self):
        send = Mock()
        with self.assertRaises(ValueError):
            notify.run(self.state, self.snapshot, self.ack, ex.START,
                       {**VALID, "TELEGRAM_V32_BOT_TOKEN": ""}, send)
        send.assert_not_called()
        self.assertFalse(Path(self.ack).exists())

    def test_hourly_digest_not_every_five_minutes(self):
        send = Mock()
        self.assertEqual(notify.run(self.state, self.snapshot, self.ack,
                                    ex.START + timedelta(minutes=1), VALID, send), 1)
        self.assertEqual(notify.run(self.state, self.snapshot, self.ack,
                                    ex.START + timedelta(minutes=6), VALID, send), 1)
        self.assertEqual(notify.run(self.state, self.snapshot, self.ack,
                                    ex.START + timedelta(minutes=11), VALID, send), 0)

    def test_deep_and_near_same_paper_trade_independently_announced(self):
        p = {
            "ticker": "TEST", "title": "Test", "side": "yes", "qty": 4,
            "price": "0.9800", "cost_usd": "3.93",
            "entry_time": ex.START.isoformat(), "fee_estimated_usd": "0.01",
        }
        for name in self.portfolio["portfolios"]:
            self.portfolio["portfolios"][name]["positions"] = [p]
        due = notify.due_messages(self.portfolio, {"sent": []}, ex.START)
        self.assertEqual(sum(k.startswith("buy|") for k, _ in due), 2)

    def test_quote_alerts_observe_near_and_long_without_claiming_paper_fills(self):
        near = {
            "ticker": "TEST-1", "title": "Near test", "side": "no",
            "sampled_ask": "0.9900", "available_at_best_ask": 10,
            "hours_to_close": 0.2, "hypothetical_qty": 4,
            "win_profit_taker_usd": "0.03", "loss_taker_usd": "3.97"
        }
        far = dict(near, ticker="TEST-FAR", hours_to_close=2)
        lst = notify.due_messages(self.portfolio, {"sent": []}, ex.START,
                                  {"quotes": [far, near]})
        quotes = [(k, v) for k, v in lst if k.startswith("quote|")]
        self.assertEqual(len(quotes), 2)
        self.assertIn("НЕ ПОКУПКА", quotes[0][1])
        lst2 = notify.due_messages(self.portfolio,
                                   {"sent": ["initial"] + [k for k, _ in quotes]},
                                   ex.START + timedelta(minutes=1),
                                   {"quotes": [near]})
        self.assertFalse(any(x.startswith("quote|") for x, _ in lst2))

    def test_scan_heartbeat_from_real_snapshot_once_per_bucket(self):
        snapshot = {
            "sampled_at_utc": (ex.START + timedelta(minutes=10)).isoformat(),
            "bucket": 2, "active_market_rows": 5000,
            "indicative_candidates": 750, "orderbooks_requested": 60,
            "verified_quotes": 30,
            "quotes": [{"ticker": "LONG", "title": "Example long dated contract",
                        "side": "yes", "sampled_ask": "0.9800",
                        "hours_to_close": 42.0,
                        "available_at_best_ask": 4,
                        "hypothetical_qty": 4, "win_profit_taker_usd": "0.07",
                        "loss_taker_usd": "3.93"}],
        }
        Path(self.snapshot).write_text(json.dumps(snapshot) + "\n")
        send = Mock()
        now = ex.START + timedelta(minutes=11)
        count = notify.run(self.state, self.snapshot, self.ack, now, VALID, send)
        self.assertEqual(count, 3)  # initial + real scan receipt + long quote
        messages = [call.args[2] for call in send.call_args_list]
        self.assertTrue(any("ПРОВЕРКА РЫНКА" in m and "42.0ч" in m for m in messages))
        self.assertTrue(any("НАБЛЮДЕНИЕ, НЕ ПОКУПКА" in m for m in messages))
        self.assertEqual(notify.run(self.state, self.snapshot, self.ack, now, VALID, send), 0)

    def test_stale_snapshot_never_emits_false_heartbeat(self):
        snapshot = {
            "sampled_at_utc": ex.START.isoformat(),
            "bucket": 0, "active_market_rows": 0,
            "indicative_candidates": 0, "orderbooks_requested": 0,
            "verified_quotes": 0, "quotes": [],
        }
        Path(self.snapshot).write_text(json.dumps(snapshot) + "\n")
        send = Mock()
        now = ex.START + timedelta(minutes=45)
        self.assertEqual(notify.run(self.state, self.snapshot, self.ack, now, VALID, send), 1)
        self.assertNotIn("ПРОВЕРКА РЫНКА", send.call_args.args[2])

    def test_failed_send_does_not_ack(self):
        def fail(*args):
            raise RuntimeError("simulated failure")
        with self.assertRaises(RuntimeError):
            notify.run(self.state, self.snapshot, self.ack, ex.START, VALID, fail)
        self.assertFalse(Path(self.ack).exists())

    def test_final_only_once(self):
        now = ex.END + timedelta(minutes=1)
        due = notify.due_messages(self.portfolio, {"sent": ["initial"]}, now)
        self.assertEqual([k for k, _ in due], ["final"])
        self.assertEqual(notify.due_messages(self.portfolio,
                         {"sent": ["initial", "final"]}, now), [])

    def test_never_mutates_trade_state(self):
        sender = Mock()
        before = Path(self.state).read_bytes()
        notify.run(self.state, self.snapshot, self.ack, ex.START, VALID, sender)
        self.assertEqual(Path(self.state).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
