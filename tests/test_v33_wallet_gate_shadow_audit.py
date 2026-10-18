"""Unit tests for bounded v2 diagnosis; zero network calls."""
import unittest
from unittest import mock

from tools import v33_wallet_gate_shadow_audit as audit

W = "0x" + "a" * 40
NOW = 1792223000


class ShadowAuditTests(unittest.TestCase):
    def test_timestamp_future_is_not_silently_normalized(self):
        self.assertEqual(audit.timestamp_check((NOW + 900) * 1000, NOW)["state"],
                         "future")
        self.assertEqual(audit.timestamp_check((NOW - 50) * 1000, NOW)["state"],
                         "valid")
        self.assertEqual(audit.timestamp_check(NOW - 50, NOW)["unit"], "seconds")
        self.assertEqual(audit.timestamp_check(NOW * 1000000, NOW)["state"],
                         "unsupported_large_unit")
        self.assertEqual(audit.timestamp_check("2026-10-17T20:00:00Z", NOW)["state"],
                         "unsupported_format")

    def test_unredeemed_loss_is_counted_as_observed_not_combined_legacy(self):
        rows = [
            {"last_event_at": NOW, "status": "REDEEMABLE", "redeemable": True,
             "realized_pnl": 0, "unrealized_pnl": -125},
            {"last_event_at": (NOW + 900) * 1000, "status": "REDEEMABLE",
             "redeemable": True, "realized_pnl": 1, "unrealized_pnl": -2},
        ]
        page = audit.position_page(rows, "REDEEMABLE_LOST", NOW)
        self.assertEqual(page["redeemable_negative_rows"], 2)
        self.assertEqual(page["redeemable_major_loss_rows"], 1)
        self.assertEqual(page["invalid_timestamps_sample"][0]["state"], "future")

    def test_incomplete_page_never_promotes_wallet(self):
        response = [
            {"data": [{"last_event_at": NOW, "status": "CLOSED"}],
             "pagination": {"has_more": False}},
            {"data": [{"last_event_at": NOW, "status": "REDEEMABLE",
                       "redeemable": True, "realized_pnl": 0,
                       "unrealized_pnl": -500}],
             "pagination": {"has_more": True, "next_cursor": "next"}},
        ]
        fetch = mock.Mock(side_effect=response)
        data = audit.audit_one(W, {"closed": 100, "profitable_closed_rate": .98,
                                   "profit_factor": 12}, NOW, fetch)
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(data["pages"]["CLOSED"]["pagination_complete"])
        self.assertFalse(data["pages"]["REDEEMABLE_LOST"]["pagination_complete"])
        self.assertFalse(data["usable_for_promoting_wallet"])
        self.assertFalse(data["full_economic_ledger_verified"])
        self.assertEqual(data["pages"]["REDEEMABLE_LOST"]
                         ["redeemable_major_loss_rows"], 1)
        for call in fetch.call_args_list:
            self.assertEqual(call.args[0], "/v2/positions")
            self.assertEqual(call.args[1]["limit"], audit.PAGE_LIMIT)

    def test_failed_api_is_an_unknown_not_a_zero_loss(self):
        fetch = mock.Mock(side_effect=TimeoutError("offline"))
        result = audit.audit_one(W, {}, NOW, fetch)
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(result["full_economic_ledger_verified"])
        self.assertNotIn("redeemable_negative_rows",
                         result["pages"]["REDEEMABLE_LOST"])
        self.assertEqual(result["pages"]["REDEEMABLE_LOST"]["api_or_schema_error"],
                         "TimeoutError")

    def test_focus_wallets_remain_bounded(self):
        prefixes = audit.FOCUS_PREFIXES
        data = {"shortlist": [{"wallet": p + "a" * (42 - len(p)),
                               "closed": 100} for p in prefixes]}
        selected = audit.choose_wallets(data)
        self.assertEqual(len(selected), len(prefixes))


if __name__ == "__main__":
    unittest.main()
