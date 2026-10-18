"""Read-only probe tests: no public API, Telegram, or trading."""
import unittest
from unittest import mock

from tools import v33_shadow_resolution_probe as probe

W="0x"+"a"*40
C="0x"+"b"*64
K=W+"|"+C+"|123"


class ShadowProbeTests(unittest.TestCase):
    def test_newly_classified_outcome_and_quote_are_reported_separately(self):
        state={"version":33,"shadow_evidence":{K:{"quoted_at":1000,
                                                       "decision":"blocked_recent"}}}
        truth={"schema":"v33_true_sports_audit_1","updated_at":1010,
               "shadows":{"rows":[]}}
        qualified=[{"wallet":W,"condition":C,"token_id":"123",
                    "result":"loss","original_book_quote":{"book_quoted_at":1000}}]
        with mock.patch.object(probe.audit, "audit_signals",
                               return_value=qualified) as f:
            data=probe.run(state,truth,check_now=2000)
        self.assertEqual(f.call_args.kwargs["max_checks"],10)
        self.assertTrue(f.call_args.kwargs["historical_shadow"])
        self.assertEqual(data["newly_verified_resolved_loss"],1)
        self.assertEqual(data["newly_verified_by_observed_shadow_decision"],
                         {"blocked_recent_loss":1})
        self.assertEqual(data["newly_verified_with_original_quote"],1)
        self.assertEqual(data["rows_with_original_book_quote"],1)
        self.assertTrue(data["nothing_written_to_source"])
        self.assertEqual(data["orders_placed"],0)

    def test_prior_classification_is_not_mislabeled_new(self):
        state={"version":33,"shadow_evidence":{}}
        original={"wallet":W,"condition":C,"token_id":"123","result":"win"}
        truth={"schema":"v33_true_sports_audit_1",
               "shadows":{"rows":[original]}}
        with mock.patch.object(probe.audit,"audit_signals",
                               return_value=[original]):
            data=probe.run(state,truth,check_now=2000)
        self.assertEqual(data["newly_verified_resolved_win"],0)
        self.assertEqual(data["records_missing_quote_proof"],1)

    def test_corrupt_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            probe.run({"version":32},{"schema":"v33_true_sports_audit_1"})
        with self.assertRaises(ValueError):
            probe.run({"version":33},{"schema":"other"})
