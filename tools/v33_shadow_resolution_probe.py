#!/usr/bin/env python3
"""One bounded read-only smoke of shadow outcome backlog against public v2.

Uses a snapshotted truth file and contemporaneous quote journal; outputs ONLY
aggregate diagnostics. No Telegram credentials, state commits, or real orders.
"""
import collections
import json
import os
import time
from pathlib import Path

from tools import v33_result_audit as audit

ROOT = Path(os.getenv("V33_SHADOW_PROBE_INPUT_DIR", "v33-snapshot"))
OUTPUT = Path("artifacts/v33-shadow-resolution-probe.json")
MAX_PROBES = 10


def run(state, truth, check_now=None):
    check_now = int(check_now if check_now is not None else time.time())
    if not isinstance(state, dict) or state.get("version") != 33:
        raise ValueError("Unknown live-state schema")
    if not isinstance(truth, dict) or truth.get("schema") != "v33_true_sports_audit_1":
        raise ValueError("Unknown truth audit schema")
    observed = state.get("shadow_evidence") or {}
    old_rows = (truth.get("shadows") or {}).get("rows") or []
    if not isinstance(observed, dict) or not isinstance(old_rows, list):
        raise ValueError("Malformed evidence/journal")
    sent = {}
    for key, item in observed.items():
        if not isinstance(item, dict):
            continue
        try:
            timestamp = int(item.get("quoted_at") or 0)
        except (ValueError,TypeError,OverflowError):
            continue
        if 0 < timestamp <= check_now + 300:
            sent[key] = timestamp
    previous = {(x.get("wallet"), x.get("condition"), x.get("token_id")): x
                for x in old_rows if isinstance(x, dict)}
    rows = audit.audit_signals(sent, {}, {}, old_rows, observed, max_checks=MAX_PROBES,
                               historical_shadow=True)
    prior_resolved = {(k): x.get("result") for k,x in previous.items()}
    newly_resolved = [
        x for x in rows if x.get("result") in ("win","loss") and
        prior_resolved.get((x.get("wallet"),x.get("condition"),x.get("token_id")))
        not in ("win","loss")]
    def decision_for(row):
        key = "|".join((row.get("wallet") or "",
                        row.get("condition") or "", row.get("token_id") or ""))
        from_live = observed.get(key)
        if isinstance(from_live, dict) and from_live.get("decision"):
            return str(from_live["decision"])
        older = previous.get((row.get("wallet"), row.get("condition"),
                              row.get("token_id"))) or {}
        return str(older.get("shadow_decision") or row.get("shadow_decision")
                   or "UNKNOWN_DECISION")
    newly_by_decision = collections.Counter(
        decision_for(row)+"_"+str(row.get("result")) for row in newly_resolved)
    newly_with_quote = sum(
        isinstance(row.get("original_book_quote"), dict) for row in newly_resolved)
    statuses = collections.Counter(str(x.get("result")) for x in rows)
    total_quote_proof = sum(
        isinstance(x.get("original_book_quote"), dict) for x in rows)
    result = {
        "schema": "v33_shadow_probe_1",
        "observed_at": check_now,
        "source_truth_updated_at": truth.get("updated_at"),
        "visible_current_shadow_keys": len(sent),
        "previous_truth_rows": len(old_rows),
        "retained_rows_after_bounded_replay": len(rows),
        "newly_verified_resolved_win": sum(x.get("result")=="win" for x in newly_resolved),
        "newly_verified_resolved_loss": sum(x.get("result")=="loss" for x in newly_resolved),
        "newly_verified_by_observed_shadow_decision": dict(newly_by_decision),
        "newly_verified_with_original_quote": newly_with_quote,
        "all_retained_status_counts": dict(statuses),
        "rows_with_original_book_quote": total_quote_proof,
        "records_missing_quote_proof": len(rows)-total_quote_proof,
        "maximum_new_public_outcome_classifications": MAX_PROBES,
        "actual_telegram_sends": 0, "orders_placed": 0,
        "real_copy_fill_verified": False,
        "nothing_written_to_source": True,
        "recovered_historical_observations_are_not_strategy_roi": True,
    }
    return result


def main():
    state = json.loads((ROOT/"research/v33_signal_state.json").read_text())
    truth = json.loads((ROOT/"research/v33_truth_report.json").read_text())
    result = run(state, truth)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n")
    print("V33 SHADOW RESOLUTION PROBE", json.dumps(result, sort_keys=True))
    print("PUBLIC READ-ONLY: 10 MAX OUTCOME CLASSIFICATIONS, NO ORDERS/TELEGRAM")


if __name__=="__main__":
    main()
