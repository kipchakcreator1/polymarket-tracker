"""No-network safety and 12-hour experiment tests."""
import json
import tempfile
import unittest
from datetime import timedelta, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from kalshi99 import experiment12h as ex


def market(ticker, hours, ask="0.98"):
    return {
        "status": "active", "market_type": "binary",
        "ticker": ticker, "title": "TEST " + ticker,
        "close_time": (ex.START + timedelta(hours=hours)).isoformat(),
        "yes_ask_dollars": ask, "no_ask_dollars": "0.03",
    }


def quote(ticker, hours=0.5):
    return {
        "ticker": ticker, "title": "TEST " + ticker, "side": "yes",
        "quote": "0.98", "hours_to_close": hours,
        "depth_contracts": 5, "paper_contracts": 4,
        "estimated_fee": "0.01", "paper_cost": "3.93",
        "profit_if_yes_correct": "0.07", "loss_if_wrong": "3.93",
        "break_even_probability": 0.9825,
        "close_time": (ex.START + timedelta(hours=hours)).isoformat(),
    }


class Experiment12HourTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = {
            "state_path": str(root / "state.json"),
            "snapshots_path": str(root / "samples.jsonl"),
            "report_path": str(root / "latest.json"),
            "final_path": str(root / "final.json"),
        }

    def run_at(self, timestamp):
        return ex.run(timestamp, **self.paths)

    def test_exact_12_hour_duration(self):
        self.assertEqual(int((ex.END - ex.START).total_seconds()), 43200)

    def test_start_waits_without_market_api(self):
        with patch.object(ex.base, "list_open_markets") as api:
            report = self.run_at(ex.START - timedelta(seconds=1))
        api.assert_not_called()
        self.assertEqual(report["status"], "WAITING_FOR_START")

    def test_end_stops_scan_and_writes_final_report(self):
        with patch.object(ex.base, "list_open_markets") as api:
            report = self.run_at(ex.END)
            again = self.run_at(ex.END + timedelta(minutes=5))
        api.assert_not_called()
        self.assertEqual(report["status"], "FINISHED_12H")
        self.assertEqual(again["status"], "FINISHED_12H")
        self.assertEqual(report["scans_completed"], 0)
        self.assertTrue(Path(self.paths["final_path"]).exists())

    def test_two_independent_virtual_4usd_banks_one_scan(self):
        ms = [market("TEST-NEAR", 0.5)]
        qs = [quote("TEST-NEAR")]
        with patch.object(ex.base, "list_open_markets", return_value=(ms, 1)) as api:
            with patch.object(ex.base, "quote_candidates", return_value=(qs, 0)):
                result = self.run_at(ex.START + timedelta(minutes=1))
        api.assert_called_once()
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["scans_completed"], 1)
        self.assertEqual(result["unique_verified_market_sides"], 1)
        self.assertEqual(result["portfolios"]["taker_12h"]["cash_usd"], "0.07")
        self.assertEqual(result["portfolios"]["taker_near_1h"]["cash_usd"], "0.07")
        self.assertEqual(result["maker_filled_simulated"], 0)
        rows = Path(self.paths["snapshots_path"]).read_text().splitlines()
        self.assertEqual(len(rows), 1)
        record = json.loads(rows[0])
        self.assertEqual(record["quotes"][0]["maker_limit_1c_below_ask"], "0.97")
        self.assertFalse(record["quotes"][0]["maker_fill_observed"])

    def test_one_entry_no_repeat_in_same_bucket(self):
        with patch.object(ex.base, "list_open_markets", return_value=([market("TEST-NEAR", 0.5)], 1)) as api:
            with patch.object(ex.base, "quote_candidates", return_value=([quote("TEST-NEAR")], 0)):
                self.run_at(ex.START + timedelta(minutes=1))
                later = self.run_at(ex.START + timedelta(minutes=2))
        self.assertEqual(api.call_count, 1)
        self.assertEqual(later["status"], "ALREADY_SCANNED_BUCKET")
        self.assertEqual(later["scans_completed"], 1)

    def test_near_portfolio_only_under_one_hour(self):
        ms = [market("TEST-SLOW", 3.0)]
        qs = [quote("TEST-SLOW", 3.0)]
        with patch.object(ex.base, "list_open_markets", return_value=(ms, 1)):
            with patch.object(ex.base, "quote_candidates", return_value=(qs, 0)):
                result = self.run_at(ex.START + timedelta(minutes=1))
        self.assertEqual(result["portfolios"]["taker_12h"]["open"], 1)
        self.assertEqual(result["portfolios"]["taker_near_1h"]["open"], 0)

    def test_already_existing_99c_quote_closing_42_hours_later_is_paper_bought_now(self):
        # The 12-hour limit governs the STUDY, not how long a contract may remain open.
        ms = [market("TEST-LONG", 42, "0.99")]
        qs = [quote("TEST-LONG", 42)]
        with patch.object(ex.base, "list_open_markets", return_value=(ms, 1)):
            with patch.object(ex.base, "quote_candidates", return_value=(qs, 0)):
                outcome = self.run_at(ex.START + timedelta(minutes=1))
        self.assertEqual(outcome["status"], "ACTIVE")
        self.assertEqual(outcome["portfolios"]["taker_12h"]["open"], 1)
        self.assertEqual(outcome["portfolios"]["taker_12h"]["cash_usd"], "0.07")
        self.assertEqual(outcome["portfolios"]["taker_near_1h"]["open"], 0)
        self.assertEqual(outcome["latest_scan"]["new_paper_positions"]["taker_12h"]["ticker"], "TEST-LONG")
        self.assertEqual(outcome["portfolios"]["taker_12h"]["realized_pnl_usd"], "0.00")

    def test_no_prices_means_no_fabricated_position(self):
        ms = [market("TEST-ONE", 0.5)]
        with patch.object(ex.base, "list_open_markets", return_value=(ms, 1)):
            with patch.object(ex.base, "quote_candidates", return_value=([], 0)):
                result = self.run_at(ex.START + timedelta(minutes=1))
        self.assertEqual(result["portfolios"]["taker_12h"]["open"], 0)
        self.assertEqual(result["portfolios"]["taker_near_1h"]["open"], 0)
        self.assertEqual(result["verified_quotes_cumulative"], 0)

    def test_realized_pnl_requires_official_finalization(self):
        ms = [market("TEST-NEAR", 0.5)]
        q = [quote("TEST-NEAR")]
        with patch.object(ex.base, "list_open_markets", return_value=(ms, 1)):
            with patch.object(ex.base, "quote_candidates", return_value=(q, 0)):
                self.run_at(ex.START + timedelta(minutes=1))
        def official(endpoint, *args, **kwargs):
            return {"market": {"status": "finalized", "result": "yes"}}
        with patch.object(ex.base, "get_json", side_effect=official):
            with patch.object(ex.base, "list_open_markets", return_value=([], 1)):
                report = self.run_at(ex.START + timedelta(minutes=6))
        for n in ("taker_12h", "taker_near_1h"):
            p = report["portfolios"][n]
            self.assertEqual(p["settled"], 1)
            self.assertEqual(p["wins"], 1)
            self.assertEqual(p["cash_usd"], "4.07")
            self.assertEqual(p["realized_pnl_usd"], "0.07")

    def test_failed_scan_cannot_save_fabricated_fill(self):
        with patch.object(ex.base, "list_open_markets", side_effect=RuntimeError("API down")):
            with self.assertRaisesRegex(RuntimeError, "API down"):
                self.run_at(ex.START + timedelta(minutes=1))
        self.assertFalse(Path(self.paths["state_path"]).exists())
        report = json.loads(Path(self.paths["report_path"]).read_text())
        self.assertEqual(report["status"], "SCAN_FAILED")

    def test_pending_positions_not_counted_as_profit_at_cutoff(self):
        ms = [market("TEST-NEAR", 0.5)]
        with patch.object(ex.base, "list_open_markets", return_value=(ms, 1)):
            with patch.object(ex.base, "quote_candidates", return_value=([quote("TEST-NEAR")], 0)):
                self.run_at(ex.START + timedelta(minutes=1))
        with patch.object(ex.base, "get_json") as api:
            finished = self.run_at(ex.END + timedelta(seconds=1))
        api.assert_not_called()
        self.assertEqual(finished["portfolios"]["taker_12h"]["open"], 1)
        self.assertEqual(finished["portfolios"]["taker_12h"]["settled"], 0)
        self.assertEqual(finished["portfolios"]["taker_12h"]["realized_pnl_usd"], "0.00")


if __name__ == "__main__":
    unittest.main()
