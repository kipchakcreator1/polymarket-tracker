"""Offline notification tests. NEVER contacts Telegram or Kalshi."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from kalshi99 import telegram_report as t
from kalshi99 import scanner as s


VALID = {
    "TELEGRAM_V31_BOT_TOKEN": "old-test-bot",
    "TELEGRAM_V32_BOT_TOKEN": "new-test-bot",
    "TELEGRAM_CHAT_ID": "123456789",
}


def sample_position():
    return {
        "title": "Demo event",
        "ticker": "TEST-TICKER",
        "side": "yes", "qty": 4, "price": "0.9800",
        "cost_usd": "3.93", "fee_estimated_usd": "0.01",
        "entry_time": "2026-10-14T05:41:03+00:00",
    }


class TelegramPaperTests(unittest.TestCase):
    def test_private_id_can_be_reused_with_distinct_bot_token(self):
        self.assertEqual(t.validated_destination(VALID), ("new-test-bot", "123456789"))

    def test_missing_second_token_never_falls_back_to_prod(self):
        with self.assertRaises(ValueError):
            t.validated_destination({**VALID, "TELEGRAM_V32_BOT_TOKEN": ""})

    def test_same_token_never_uses_production_bot(self):
        with self.assertRaises(ValueError):
            t.validated_destination({**VALID, "TELEGRAM_V32_BOT_TOKEN": "old-test-bot"})

    def test_missing_or_invalid_chat_id_fails(self):
        for v in ("", "not-an-id", "https://t.me/foo"):
            with self.subTest(v=v), self.assertRaises(ValueError):
                t.validated_destination({**VALID, "TELEGRAM_CHAT_ID": v})

    def test_first_position_sends_once_without_spending_real_money(self):
        state = s.new_state()
        state["cash_usd"] = "0.07"
        state["positions"] = [sample_position()]
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            state_path = p / "state.json"
            cp = p / "ack.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sender = Mock()
            date = datetime(2026, 10, 14, 5, 45, tzinfo=timezone.utc)
            self.assertEqual(t.run(str(state_path), str(cp), VALID, date, sender), 1)
            message = sender.call_args.args[2]
            self.assertIn("KALSHI", message)
            self.assertIn("VIRTUAL BUY", message)
            self.assertIn("$3.93", message)
            self.assertIn("$0.07", message)
            self.assertIn("No real orders", message)
            self.assertEqual(t.run(str(state_path), str(cp), VALID, date, sender), 0)
            sender.assert_called_once()
            self.assertEqual(json.loads(state_path.read_text()), state)

    def test_official_settlement_only_notifies_once(self):
        state = s.new_state()
        pos = {**sample_position(), "result": "yes", "won": True,
               "realized_pnl_usd": "0.07", "settled_at": "2026-10-20T17:10:00+00:00"}
        state["history"] = [pos]
        state["cash_usd"] = "4.07"
        now = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)
        ack = {"schema": 1, "sent_ids": [t.event_key(pos, "buy")]}
        self.assertEqual(t.notifications_due(state, ack, now)[0][0], t.event_key(pos, "settled"))
        text = t.notifications_due(state, ack, now)[0][1]
        self.assertIn("PAPER WIN", text)
        self.assertIn("+$0.07", text)

    def test_daily_update_only_after_15utc_once(self):
        state = s.new_state()
        before = datetime(2026, 10, 14, 14, 59, tzinfo=timezone.utc)
        after = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(t.notifications_due(state, {"sent_ids": []}, before), [])
        due = t.notifications_due(state, {"sent_ids": []}, after)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0][0], "daily|2026-10-14")
        self.assertEqual(t.notifications_due(state, {"sent_ids": ["daily|2026-10-14"]}, after), [])

    def test_failed_delivery_is_not_checkpointed(self):
        state = s.new_state()
        state["cash_usd"] = "0.07"
        state["positions"] = [sample_position()]
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            state_path = p / "state.json"
            cp = p / "ack.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            def reject(*args):
                raise RuntimeError("Telegram rejected")
            with self.assertRaises(RuntimeError):
                t.run(str(state_path), str(cp), VALID,
                      datetime(2026, 10, 14, 5, 45, tzinfo=timezone.utc), reject)
            self.assertFalse(cp.exists())

    def test_bad_state_prevents_notification(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            st, cp = p / "state", p / "ack"
            st.write_text('{"mode":"LIVE","positions":[],"history":[],"cash_usd":"4.00"}')
            sender = Mock()
            with self.assertRaises(ValueError):
                t.run(str(st), str(cp), VALID,
                      datetime(2026, 10, 14, 5, 45, tzinfo=timezone.utc), sender)
            sender.assert_not_called()

    def test_fee_price_and_loss_messages_clear(self):
        p = sample_position()
        self.assertIn("4 contracts × $0.9800", t.entry_text(p, "0.07"))
        self.assertIn("Loss: −$3.93", t.entry_text(p, "0.07"))
        h = {**p, "won": False, "result": "no", "realized_pnl_usd": "-3.93"}
        self.assertIn("LOSS ❌", t.settlement_text(h, "0.07"))
        self.assertIn("-$3.93", t.settlement_text(h, "0.07"))


if __name__ == "__main__":
    unittest.main()
