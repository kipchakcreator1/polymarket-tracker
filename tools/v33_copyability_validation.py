#!/usr/bin/env python3
"""Independent, read-only forward sports copyability diagnostics.

Consumes the already published v3.3 paper ledger and the original contemporaneous
quote journal. A v2 outcome classification is inherited, NOT reverified here.
Never infers fills, profitable wallets, signal precision, or causal filter effects.
"""
import collections
import json
import math
import os
import time
from pathlib import Path

from tools import v33_forward_net_paper as paper

INPUT = Path(os.getenv("V33_COPY_AUDIT_INPUT_DIR", "."))
OUTPUT = Path("artifacts/v33-copyability-gate.json")
MIN_RESOLVED_PER_STREAM = 30
TOLERANCE = 0.00011
OUTCOMES = ("win", "loss")
STREAMS = ("telegram_sent", "shadow_only")


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _identity(row):
    if not isinstance(row, dict):
        return None
    wallet, condition, token = (row.get(k) for k in
                                ("wallet", "condition", "token_id"))
    if not all(isinstance(v, str) and v for v in (wallet, condition, token)):
        return None
    return "|".join((wallet.lower(), condition.lower(), token))


def _source_evidence(state, row):
    """Only timestamps present in original forward quote evidence are admitted."""
    key = _identity(row)
    if not key:
        return None
    stream = row.get("stream")
    collection = "signal_evidence" if stream == "telegram_sent" else "shadow_evidence"
    saved = (state.get(collection) or {}).get(key)
    if not isinstance(saved, dict):
        return None
    when = _number(saved.get("source_trade_at"))
    quoted = _number(row.get("book_quoted_at"))
    if when is None or quoted is None:
        return None
    duration = quoted - when
    # Retain trace of impossible clock/order case, never alter timestamp.
    return {"source_trade_at": int(when),
            "observed_source_to_quote_seconds": round(duration, 1),
            "chronology_plausible": 0 <= duration <= 300,
            "same_snapshot_quote_time": abs(quoted - (
                _number(saved.get("book_quoted_at")) or -1)) <= 2}


def _validate_paper(row):
    """Recalculate all paper scenario totals from ask and outcome, not from PnL labels."""
    if row.get("status") != "verified_settled_paper":
        return None, "not_settled"
    if row.get("stream") not in STREAMS or row.get("final_outcome") not in OUTCOMES:
        return None, "invalid_stream_or_outcome"
    if row.get("real_trading_fill") is not False:
        return None, "not_explicitly_unfilled"
    ask = _number(row.get("ask_vwap"), positive=True)
    usd = _number(row.get("stake_usd"), positive=True)
    shares = _number(row.get("visible_book_shares"), positive=True)
    when = _number(row.get("book_quoted_at"), positive=True)
    if (ask is None or not 0 < ask < 1 or usd is None or not usd <= 100
            or shares is None or when is None):
        return None, "invalid_or_missing_original_quote"
    if abs(shares*ask-usd) > max(.025, usd*.025):
        return None, "book_share_cost_mismatch"
    outcome = row["final_outcome"] == "win"
    observed = row.get("hypothetical_pnl_usd")
    if not isinstance(observed, dict):
        return None, "scenario_missing"
    scenarios = {
        "fee_zero": (0.0, 1.0, "pnl_fee_rate_0.00"),
        "fee_5pct": (.05, 1.0, "pnl_fee_rate_0.05"),
        "fee_7pct": (.07, 1.0, "pnl_fee_rate_0.07"),
        "fee_5pct_plus_5pct_worse_entry": (
            .05, 1.05, "pnl_fee_0.05_plus_5pct_worse_entry"),
    }
    pnl = {}
    for label, (fee_rate, deterioration, k) in scenarios.items():
        expected = paper.paper_pnl(usd, ask, outcome, fee_rate, deterioration)
        recorded = _number(observed.get(k))
        if expected is None:
            return None, "scenario_unpriceable"
        if recorded is None or abs(expected-recorded) > TOLERANCE:
            return None, "scenario_inconsistent_with_quote"
        pnl[label] = round(expected, 5)
    return pnl, None


def _summary(rows):
    amount = sum(row["stake_usd"] for row in rows)
    result = {
        "distinct_market_conditions_with_observed_settlement": len(rows),
        "paper_stake_usd": round(amount, 4),
        "winning_conditions": sum(x["outcome"] == "win" for x in rows),
        "losing_conditions": sum(x["outcome"] == "loss" for x in rows),
        "min_30_distinct_market_conditions": len(rows) >= MIN_RESOLVED_PER_STREAM,
        "true_independent_sports_events_certified": False,
        "actual_fills": 0,
        "actual_copy_net_roi": None,
        "source_trade_to_book_quote_latency_seconds_median": None,
        "scenario_results": {},
    }
    durations = sorted(x["latency"] for x in rows if x["latency"] is not None)
    if durations:
        n = len(durations)
        result["source_trade_to_book_quote_latency_seconds_median"] = (
            (durations[n//2] if n%2 else
             (durations[n//2-1]+durations[n//2])/2))
    for scenario in ("fee_zero", "fee_5pct", "fee_7pct",
                     "fee_5pct_plus_5pct_worse_entry"):
        total = sum(x["pnl"][scenario] for x in rows)
        result["scenario_results"][scenario] = {
            "model_hold_pnl_usd": round(total, 5) if amount else None,
            "model_hold_roi": round(total/amount, 6) if amount else None,
            "qualified_for_descriptive_sample": bool(
                amount and len(rows) >= MIN_RESOLVED_PER_STREAM),
            "actual_execution_roi": None,
        }
    return result


def build_report(forward, state=None, now=None):
    if not isinstance(forward, dict) or forward.get("schema") != "v33_forward_net_paper_1":
        raise ValueError("Unknown forward paper schema")
    if forward.get("forward_only_no_backfilled_quotes") is not True:
        raise ValueError("Provenance of forward book quotes is not certified")
    original = forward.get("positions")
    if not isinstance(original, list):
        raise ValueError("Missing original paper rows")
    state = state if isinstance(state, dict) else {}
    status_counts = collections.Counter()
    cohort_counts = collections.Counter()
    decision_counts = collections.Counter()
    errors = collections.Counter()
    candidates = []
    for original_row in original:
        if not isinstance(original_row, dict):
            errors["invalid_row"] += 1
            continue
        stream = original_row.get("stream")
        status_counts[str(original_row.get("status"))] += 1
        if stream in STREAMS:
            cohort_counts[stream] += 1
            if original_row.get("status") == "verified_settled_paper":
                decision_counts[str(original_row.get("decision"))] += 1
        pnl, reason = _validate_paper(original_row)
        if reason == "not_settled":
            continue
        if reason:
            errors[reason] += 1
            continue
        key = _identity(original_row)
        if not key:
            errors["missing_quote_identity"] += 1
            continue
        timing = _source_evidence(state, original_row)
        if timing and not timing["chronology_plausible"]:
            errors["impossible_quote_chronology"] += 1
            continue
        if timing and not timing["same_snapshot_quote_time"]:
            errors["quote_journal_timestamp_mismatch"] += 1
            continue
        candidates.append({
            "stream": stream, "identity": key,
            "condition": original_row["condition"].lower(),
            "token": original_row["token_id"],
            "wallet": original_row["wallet"].lower(),
            "outcome": original_row["final_outcome"],
            "stake_usd": float(original_row["stake_usd"]),
            "pnl": pnl,
            "latency": timing["observed_source_to_quote_seconds"] if timing else None,
        })
    # Exclude all repeated condition IDs from descriptive aggregate. Different
    # wallets/opposing tokens on the same market are NOT independent events.
    conditions = collections.Counter(x["condition"] for x in candidates)
    ambiguous = sum(v for v in conditions.values() if v > 1)
    rows = [x for x in candidates if conditions[x["condition"]] == 1]
    by_stream = {s: _summary([x for x in rows if x["stream"] == s])
                 for s in STREAMS}
    sent = by_stream["telegram_sent"]["distinct_market_conditions_with_observed_settlement"]
    shadow = by_stream["shadow_only"]["distinct_market_conditions_with_observed_settlement"]
    matched = {s: cohort_counts[s] for s in STREAMS}
    comparison_possible = sent >= MIN_RESOLVED_PER_STREAM and shadow >= MIN_RESOLVED_PER_STREAM
    flags = []
    if not sent:
        flags.append("no_unique_resolved_sent_paper")
    if not shadow:
        flags.append("no_unique_resolved_rejected_paper")
    if not comparison_possible:
        flags.append("insufficient_outcomes_for_two_cohort_descriptive_comparison")
    flags.extend([
        "filter_quality_not_causally_identified",
        "rolling_quote_journal_survivorship_bias",
        "event_level_independence_not_certified",
        "published_market_fee_schedule_not_checked",
        "order_fills_fees_exits_not_observed",
        "outcomes_inherited_from_v33_truth_audit_not_independently_refetched",
    ])
    return {
        "schema": "v33_copyability_validation_1",
        "generated_at": int(now if now is not None else time.time()),
        "official_outcome_snapshot_at": forward.get("official_source_updated_at"),
        "source": "forward_book_quote_and_official_settlement_inherited",
        "rows_in_forward_paper": len(original),
        "raw_stream_counts": dict(matched),
        "raw_status_counts": dict(status_counts),
        "raw_settled_decision_counts": dict(decision_counts),
        "raw_settled_rows": status_counts["verified_settled_paper"],
        "validated_settled_quote_rows": len(candidates),
        "duplicated_market_quote_rows_excluded": ambiguous,
        "descriptive_by_stream": by_stream,
        "paired_stream_comparison_ready": comparison_possible,
        "causal_impact_of_filter_estimated": False,
        "risk_gate_hypothetical_false_negative_rate": None,
        "data_quality_errors": dict(errors),
        "blocking_reasons": flags,
        "wallets_live_approved": 0,
        "would_send_telegram": False,
        "would_place_orders": False,
        "note": ("All returns are hypothetical hold-to-payout on observed $5 "
                 "orderbook quotes with fee scenarios. No actual fills, "
                 "no verified independent event-level outcome sample, "
                 "and no reliable estimate of causal filtering benefit."),
    }


def main():
    f = json.loads((INPUT/"research/v33_forward_net_paper.json").read_text())
    state = json.loads((INPUT/"research/v33_signal_state.json").read_text())
    report = build_report(f, state)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    compact = {k: v for k,v in report.items() if k not in ("note",)}
    print("V33 COPYABILITY VALIDATION", json.dumps(compact, sort_keys=True))
    print("NO TELEGRAM, NO ORDERS, NO PRODUCTION STATE WRITES")


if __name__ == "__main__":
    main()
