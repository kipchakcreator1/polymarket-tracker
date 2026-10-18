#!/usr/bin/env python3
"""Durable, research-only shadow settlement archive.

Input: immutable-at-capture public book quote ring, v2 sports truth snapshot,
and previous audit checkpoint on a separate GitHub audit branch.
Output: ONLY an audit-branch JSON checkpoint. NEVER Telegram or orders.
No claims of real execution, complete market coverage or strategy ROI.
"""
import collections
import json
import math
import os
import time
from pathlib import Path
from tools import v33_result_audit as truth

SOURCE = Path(os.getenv("V33_CHECKPOINT_SOURCE_DIR", "v33-snapshot"))
OUTPUT = Path("research/v33-shadow-outcome-checkpoint.json")
SCHEMA = "v33_durable_shadow_checkpoint_1"
CHECKS_PER_RUN = max(1, min(60, int(os.getenv("V33_SHADOW_MAX_API_CHECKS", "25"))))
CONDITION_CAP = truth.SHADOW_MAX_RETAINED
RETENTION = truth.SHADOW_RETENTION_SECONDS


def key_parts(row):
    if not isinstance(row, dict):
        return None
    wallet = row.get("wallet")
    condition = row.get("condition")
    token = row.get("token_id")
    if not (isinstance(wallet, str) and isinstance(condition, str)
            and isinstance(token, str)):
        return None
    wallet, condition = wallet.lower(), condition.lower()
    if not (len(wallet) == 42 and wallet.startswith("0x")
            and all(c in "0123456789abcdef" for c in wallet[2:])
            and len(condition) == 66 and condition.startswith("0x")
            and all(c in "0123456789abcdef" for c in condition[2:])
            and token and len(token) <= 128):
        return None
    return wallet, condition, token


def valid_at(value, now):
    try:
        stamp = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return stamp if 0 < stamp <= now+300 else None


def load_source(state, report):
    if not (isinstance(state, dict) and state.get("version") == 33):
        raise ValueError("Unexpected research signal state")
    if not (isinstance(report, dict) and
            report.get("schema") == "v33_true_sports_audit_1"):
        raise ValueError("Unexpected official sports truth report")
    if not isinstance(state.get("shadow_evidence"), dict):
        raise ValueError("Missing quote ring")
    if not isinstance((report.get("shadows") or {}).get("rows"), list):
        raise ValueError("Missing historical shadow outcomes")


def safe_prev(old):
    if old is None:
        return {"schema": SCHEMA, "last_saved_at": 0, "rows": [],
                "meta": {"completed_runs": 0}}
    if not isinstance(old, dict) or old.get("schema") != SCHEMA:
        raise ValueError("Unknown checkpoint schema")
    if not isinstance(old.get("rows"), list):
        raise ValueError("Invalid checkpoint rows")
    return old


def merge_rows(previous, truth_rows, now):
    """Never replace previously archived prospective quote or final outcome."""
    saved = {}
    collisions = []
    ignored = collections.Counter()
    begin = now-RETENTION
    for origin, rows in (("checkpoint", previous), ("truth_snapshot", truth_rows)):
        for raw in rows:
            ident = key_parts(raw)
            if not ident:
                ignored["invalid_identity"] += 1
                continue
            at = valid_at(raw.get("alert_at"), now)
            if at is None or at < begin:
                ignored["outside_retention_or_bad_time"] += 1
                continue
            row = dict(raw)
            row.update(wallet=ident[0], condition=ident[1], token_id=ident[2])
            if not isinstance(row.get("original_book_quote"), dict):
                row.pop("original_book_quote", None)
                # Old truth records might contain non-audited derived profits.
                row.pop("paper_hold_pnl_no_fees", None)
                row.pop("paper_stake_usd", None)
            if ident not in saved:
                saved[ident] = row
                continue
            base = saved[ident]
            old_final = base.get("result") in ("win", "loss")
            next_final = row.get("result") in ("win", "loss")
            if old_final and next_final and base.get("result") != row.get("result"):
                collisions.append(ident)
                # Flag conflict; never change a previously saved verified result.
                continue
            # If old checkpoint has unresolved classification and newer truth
            # has a final official outcome, upgrade result, but retain quote.
            if not old_final and next_final:
                for field in ("result", "settled_at", "checked_at"):
                    base[field] = row.get(field)
            if "original_book_quote" not in base and "original_book_quote" in row:
                base["original_book_quote"] = row["original_book_quote"]
            if not base.get("shadow_decision") and row.get("shadow_decision"):
                base["shadow_decision"] = row["shadow_decision"]
    if len(saved) > CONDITION_CAP:
        # Explicit dropping is reported as a coverage breach, never silently
        # represented as complete economic or outcome evidence.
        ordered = sorted(saved.values(), key=lambda x: int(x["alert_at"]),
                         reverse=True)
        ignored["capacity_eviction"] += len(ordered)-CONDITION_CAP
        saved = {key_parts(x): x for x in ordered[:CONDITION_CAP]}
    return list(saved.values()), ignored, collisions


FINAL_DECISIONS = {
    "blocked_recent": "recent",
    "blocked_active": "active_portfolio",
    "sent_telegram": "passed",
}
INTERIM_DECISIONS = {"book_verified_only", "awaiting_recent_history"}


def capture_original_risk_verdict(row, live_quote, now):
    """Archive the prospective risk verdict, never invent older rejection causes.

    Quote and decision must refer to the SAME original market observation.
    Existing final verdicts are immutable; an interim decision may graduate
    only to a final, validated observation. The live ring is not a ledger.
    """
    if not isinstance(live_quote, dict):
        return False
    decision = live_quote.get("decision")
    expected_gate = FINAL_DECISIONS.get(decision)
    if expected_gate is None:
        return False
    try:
        first = int(row["alert_at"])
        source_quote_at = int(live_quote["quoted_at"])
        checked_at = int(live_quote["risk_checked_at"])
    except (ValueError, KeyError, TypeError, OverflowError):
        return False
    if not (abs(first-source_quote_at) <= 2 and
            first-2 <= checked_at <= now+300 and
            checked_at >= source_quote_at-2):
        return False
    if live_quote.get("risk_gate") != expected_gate:
        return False
    reason = live_quote.get("risk_reason")
    if decision == "sent_telegram":
        if reason is not None:
            return False
    elif (not isinstance(reason, str) or not 1 <= len(reason) <= 128
          or any(x in reason for x in ("\n", "\r", "|"))):
        return False
    current = row.get("shadow_decision")
    if current not in (None, "", decision, *INTERIM_DECISIONS):
        return False
    # In a few legacy records the shadow_decision was frozen from a previous
    # run but the detailed fields had not yet existed. The original live
    # evidence is still available and is contemporaneous; do not backfill
    # from historical market prices or guessed API history.
    existing = row.get("risk_gate")
    if existing is not None:
        if (existing != expected_gate or
                row.get("risk_reason") != reason):
            return False
        return True
    row["shadow_decision"] = decision
    row["risk_gate"] = expected_gate
    row["risk_reason"] = reason
    row["risk_checked_at"] = checked_at
    row["risk_provenance"] = "same_original_live_quote_at_archive_capture"
    return True


def build_checkpoint(previous, state, report, now=None,
                     verifier=None, max_checks=CHECKS_PER_RUN):
    """Verifier injection is for tests; real runs use public v2 classifier."""
    now = int(time.time() if now is None else now)
    if not 0 < max_checks <= 60:
        raise ValueError("Unsafe API budget")
    load_source(state, report)
    old = safe_prev(previous)
    previous_rows = old["rows"]
    from_truth = report["shadows"]["rows"]
    merged, ignored, collisions = merge_rows(previous_rows, from_truth, now)
    live = state["shadow_evidence"]
    sent = {}
    for key, item in live.items():
        if not isinstance(item, dict):
            continue
        parts = key.split("|")
        if len(parts) != 3:
            continue
        ident = key_parts(dict(zip(("wallet", "condition", "token_id"), parts)))
        if not ident:
            continue
        at = valid_at(item.get("quoted_at"), now)
        if at is not None and at >= now-RETENTION:
            sent["|".join(ident)] = at
    before = {key_parts(row): row.get("result") for row in merged}
    persisted_by_id = {key_parts(row): row for row in merged if key_parts(row)}
    before_keys = {"|".join(key_parts(row)) for row in merged
                   if key_parts(row)}
    verifier = verifier or truth.audit_signals
    checked = verifier(sent, {}, {}, merged, live, max_checks=max_checks,
                       historical_shadow=True)
    after = {key_parts(row): row for row in checked if key_parts(row)}
    for ident, row in after.items():
        key = "|".join(ident)
        old_persisted = persisted_by_id.get(ident)
        # v2 resolution re-query reconstructs a compact outcome row. Carry
        # forward the immutable verified source-time verdict independently
        # from its public market settlement record.
        if (isinstance(old_persisted, dict) and
                old_persisted.get("risk_provenance") ==
                "same_original_live_quote_at_archive_capture"):
            for field in ("risk_gate","risk_reason","risk_checked_at",
                          "risk_provenance"):
                if field not in row and field in old_persisted:
                    row[field] = old_persisted[field]
        current = live.get(key) or {}
        # The decision observed when the original quote was saved remains
        # stable; no later state mutation is allowed to relabel a blocked pick.
        if not row.get("shadow_decision") and isinstance(current, dict):
            decision = current.get("decision")
            if isinstance(decision, str) and decision:
                row["shadow_decision"] = decision
        # Once a contemporaneous verified reason exists, freeze it beside the
        # original quote for prospective settlement cross-referencing.
        capture_original_risk_verdict(row, current, now)
    newly_final = [row for k, row in after.items() if
                   row.get("result") in ("win", "loss") and
                   before.get(k) not in ("win", "loss")]
    statuses = collections.Counter(str(x.get("result")) for x in after.values())
    unverified = sum(x.get("result") in ("not_checked", "api_error")
                     or str(x.get("result", "")).startswith("unknown")
                     for x in after.values())
    original_quote_count = sum(isinstance(x.get("original_book_quote"), dict)
                               for x in after.values())
    reason_captured = collections.Counter(
        str(x.get("risk_gate")) for x in after.values()
        if x.get("risk_provenance") ==
        "same_original_live_quote_at_archive_capture")
    reasons_by_stage = collections.Counter(
        str(x.get("risk_gate")) + ":" + str(x.get("risk_reason"))
        for x in after.values()
        if x.get("risk_provenance") ==
        "same_original_live_quote_at_archive_capture")
    new_with_original_quote = sum(
        isinstance(x.get("original_book_quote"), dict) for x in newly_final)
    new_by_decision = collections.Counter(
        str(x.get("shadow_decision") or "unknown_decision") + "_" +
        str(x.get("result")) for x in newly_final)
    current_keys = set(sent)
    newest = max(sent.values()) if sent else None
    oldest = min(sent.values()) if sent else None
    previous_at = int(old.get("last_saved_at") or 0)
    ring_span = (newest-oldest) if newest is not None else None
    new_keys = len(current_keys-before_keys)
    elapsed = now-previous_at if previous_at > 0 else None
    input_unseen_gap = bool(elapsed and ring_span is not None and
                            elapsed > ring_span)
    missing_quote_count = len(after)-original_quote_count
    # On the FIRST run we cannot prove what the prior 900-entry ring evicted.
    # Previously saved true sports rows also may lack book quote provenance.
    first_run_history_unknown = previous_at == 0
    breach = (bool(ignored["capacity_eviction"]) or bool(collisions)
              or input_unseen_gap or first_run_history_unknown
              or missing_quote_count > 0)
    runs_per_hour = max(0, min(12, int(
        os.getenv("V33_SHADOW_SCHEDULE_RUNS_PER_HOUR", "0"))))
    capacity_per_hour = runs_per_hour*max_checks if runs_per_hour else None
    ring_rate_proxy = (round(len(sent)*3600/ring_span, 2)
                       if ring_span and ring_span > 0 else None)
    # The 10-minute scheduler needs an interval rate; do not infer it
    # from rapid code-push canaries with <5 minutes of observations.
    observed_interval_rate = (round(new_keys*3600/elapsed, 2)
                              if elapsed and elapsed >= 300 else None)
    old_backlog = (old.get("meta") or {}).get("not_yet_terminal")
    backlog_delta = (unverified-old_backlog if
                     isinstance(old_backlog, int) else None)
    meta = {
        "completed_runs": int((old.get("meta") or {}).get("completed_runs") or 0)+1,
        "source_truth_updated_at": report.get("updated_at"),
        "source_live_shadow_ring": len(live),
        "source_ring_span_seconds": ring_span,
        "observed_new_quote_keys": new_keys,
        "seconds_since_previous_checkpoint": elapsed,
        "risk_of_unobserved_ring_evictions": input_unseen_gap,
        "archived_rows": len(after),
        "archived_quotes_at_original_observation": original_quote_count,
        "captured_exact_verdicts_by_gate": dict(reason_captured),
        "captured_exact_reasons_by_gate": dict(reasons_by_stage),
        "exact_reason_unavailable_or_legacy_rows":
            len(after)-sum(reason_captured.values()),
        "missing_original_quote_rows": len(after)-original_quote_count,
        "prior_truth_rows": len(from_truth),
        "previous_checkpoint_rows": len(previous_rows),
        "newly_final_classified_this_run": len(newly_final),
        "newly_final_wins": sum(x.get("result") == "win" for x in newly_final),
        "newly_final_losses": sum(x.get("result") == "loss" for x in newly_final),
        "newly_final_by_decision": dict(new_by_decision),
        "newly_final_with_original_quote": new_with_original_quote,
        "not_yet_terminal": unverified,
        "nonterminal_backlog_change_from_previous_run": backlog_delta,
        "nonterminal_backlog_grew": (
            backlog_delta > 0 if backlog_delta is not None else None),
        "first_run_history_unknown": first_run_history_unknown,
        "ring_rate_proxy_per_hour_not_verified_arrival_rate": ring_rate_proxy,
        "actual_new_quote_keys_per_hour_since_checkpoint": observed_interval_rate,
        "configured_max_checks_per_hour": capacity_per_hour,
        "estimated_incoming_exceeds_check_capacity": (
            ring_rate_proxy > capacity_per_hour
            if ring_rate_proxy is not None and capacity_per_hour else None),
        "status_counts": dict(statuses),
        "api_checks_upper_bound": max_checks,
        "minimum_runs_to_recheck_current_backlog": math.ceil(unverified/max_checks),
        "ignored": dict(ignored),
        "terminal_outcome_conflicts": len(collisions),
        "coverage_breach_or_unknown": breach,
        "unbiased_causal_filter_evaluation_possible": False,
        "filled_order_count": 0,
        "actual_copy_roi": None,
    }
    return {"schema": SCHEMA, "last_saved_at": now,
            "rows": list(after.values()), "meta": meta,
            "disclaimer": ("Immutable quote at observation; no later price backfill. "
                           "Cannot infer real fills, winrate, missed ROI, or complete "
                           "coverage from this bounded public-API archive.")}


def main():
    state = json.loads((SOURCE/"research/v33_signal_state.json").read_text())
    report = json.loads((SOURCE/"research/v33_truth_report.json").read_text())
    previous = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else None
    checkpoint = build_checkpoint(previous, state, report)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(checkpoint, ensure_ascii=False,
                                 separators=(",",":"),allow_nan=False)+"\n")
    print("V33 DURABLE SHADOW CHECKPOINT",json.dumps(checkpoint["meta"],sort_keys=True))
    print("NO TELEGRAM; NO ORDERS; SOURCE STATE LEFT UNCHANGED")


if __name__=="__main__":
    main()
