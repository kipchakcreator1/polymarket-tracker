"""Offline tests: no network calls, no trading, no auth keys."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from kalshi99 import scanner as s


def fake_market(ticker="KXTEST-ONE", ask_yes="0.99"):
    return {
        "status": "active", "market_type": "binary",
        "ticker": ticker, "title": "Example",
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "yes_ask_dollars": ask_yes, "no_ask_dollars": "0.03",
    }


class KalshiPaperTests(unittest.TestCase):
    def test_conservative_taker_fee_and_affordable(self):
        self.assertEqual(s.fee_taker(D("0.99"), 4), D("0.01"))
        self.assertEqual(s.max_affordable(D("4.00"), D("0.99"), 4), 4)
        self.assertEqual(s.max_affordable(D("1.00"), D("0.99"), 1), 1)
        self.assertEqual(s.max_affordable(D("0.99"), D("0.99"), 1), 0)

    def test_quote_derived_from_opposite_bid_not_last_trade(self):
        book = {"orderbook_fp": {
            "yes_dollars": [["0.01", "9.00"]],
            "no_dollars": [["0.02", "4.00"], ["0.01", "10.00"]]}}
        self.assertEqual(s.top_executable_quote(book, "yes"), (D("0.98"), 4))
        self.assertEqual(s.top_executable_quote(book, "no"), (D("0.99"), 9))

    def test_empty_book_no_trade(self):
        self.assertIsNone(s.top_executable_quote({"orderbook_fp": {}}, "yes"))

    def test_skip_non_market_prices_and_closed(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(len(s.candidate_sides(fake_market(), now)), 1)
        self.assertEqual(s.candidate_sides(fake_market(ask_yes="0.95"), now), [])
        self.assertEqual(s.candidate_sides({**fake_market(), "status": "settled"}, now), [])
        self.assertEqual(s.candidate_sides({**fake_market(), "mve_collection_ticker": "foo"}, now), [])

    def test_quote_requires_real_bid_volume_at_stated_price(self):
        example = s.candidate_sides(fake_market(), datetime.now(timezone.utc))
        with patch.object(s, "get_json", return_value={
            "orderbook_fp": {"no_dollars": [["0.01", "4.00"]]}
        }):
            q, errors = s.quote_candidates(example, D("4"), 10)
        self.assertEqual(errors, 0)
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["paper_contracts"], 4)
        self.assertEqual(q[0]["paper_cost"], "3.97")
        self.assertEqual(q[0]["profit_if_yes_correct"], "0.03")
        self.assertEqual(q[0]["break_even_probability"], 0.9925)
        self.assertIsNone(q[0]["independent_probability"])

    def test_no_fabricated_fill_from_stale_or_oversized_ask(self):
        example = s.candidate_sides(fake_market(), datetime.now(timezone.utc))
        with patch.object(s, "get_json", return_value={
            "orderbook_fp": {"no_dollars": [["0.005", "100.00"]]}
        }):
            verified, _ = s.quote_candidates(example, D("4"), 10)
        self.assertEqual(verified, [])

    def test_paper_enforces_cash_and_deduplicates(self):
        state = s.new_state()
        now = datetime.now(timezone.utc)
        quote = {
            "ticker": "KX1", "title": "Example", "side": "yes", "quote": "0.99",
            "paper_contracts": 4, "paper_cost": "3.97", "estimated_fee": "0.01",
            "close_time": now.isoformat()
        }
        self.assertTrue(s.paper_enter(state, quote, now))
        self.assertEqual(state["cash_usd"], "0.03")
        self.assertFalse(s.paper_enter(state, quote, now))
        self.assertEqual(len(state["positions"]), 1)
        state["positions"] = []
        self.assertFalse(s.paper_enter(state, quote, now))

    def test_settlement_requires_official_yes_or_no(self):
        state = s.new_state()
        now = datetime.now(timezone.utc)
        quote = {
            "ticker": "KX1", "title": "Example", "side": "yes", "quote": "0.99",
            "paper_contracts": 4, "paper_cost": "3.97", "estimated_fee": "0.01",
            "close_time": now.isoformat()
        }
        s.paper_enter(state, quote, now)
        with patch.object(s, "get_json", return_value={"market": {"status": "closed", "result": "yes"}}):
            settled, errors = s.settle_positions(state)
        self.assertEqual((settled, errors), (0, 0))
        with patch.object(s, "get_json", return_value={"market": {"status": "finalized", "result": "yes"}}):
            settled, errors = s.settle_positions(state)
        self.assertEqual((settled, errors), (1, 0))
        self.assertEqual(state["cash_usd"], "4.03")
        self.assertEqual(len(state["history"]), 1)
        self.assertFalse(state["positions"])

    def test_loss_can_reduce_bankroll_to_three_cents(self):
        state = s.new_state()
        now = datetime.now(timezone.utc)
        quote = {
            "ticker": "KX2", "title": "Example", "side": "yes", "quote": "0.99",
            "paper_contracts": 4, "paper_cost": "3.97", "estimated_fee": "0.01",
            "close_time": now.isoformat()
        }
        s.paper_enter(state, quote, now)
        with patch.object(s, "get_json", return_value={"market": {"status": "finalized", "result": "no"}}):
            s.settle_positions(state)
        self.assertEqual(state["cash_usd"], "0.03")
        self.assertEqual(state["history"][0]["realized_pnl_usd"], "-3.97")

    def test_state_rejects_live_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"schema":1,"mode":"LIVE"}')
            with self.assertRaisesRegex(ValueError, "mode"):
                s.load_state(str(path))

    def test_end_to_end_stubbed_market_scan_and_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = str(Path(tmp) / "state.json")
            report_path = str(Path(tmp) / "report.json")
            class Args:
                state, report = state_path, report_path
                pages, page_size, books, horizon, paper_trade = 1, 500, 25, 72, True
            def response(endpoint, params=None, retries=2):
                if endpoint == "/markets":
                    return {"markets": [fake_market()], "cursor": ""}
                if endpoint.endswith("/orderbook"):
                    return {"orderbook_fp": {"no_dollars": [["0.01", "4.00"]]}}
                raise AssertionError("Unexpected endpoint: " + endpoint)
            with patch.object(s, "get_json", side_effect=response):
                result = s.run(Args)
            self.assertTrue(result["paper_entry_this_pass"])
            self.assertEqual(result["cash_usd"], "0.03")
            self.assertEqual(result["verified_quotes"][0]["quote"], "0.99")
            self.assertTrue(Path(report_path).exists())


if __name__ == "__main__":
    unittest.main()
