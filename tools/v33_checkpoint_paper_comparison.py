#!/usr/bin/env python3
"""Research-only replay of preserved rejected quotes vs independently checked sent quotes.

Not a causal filter experiment, not market-executable fill evidence, and not
an estimate of real copy ROI. The two cohorts can differ by league/time.
Never replace a missing entry quote with historical market prices.
"""
import collections
import json
import math
import os
from pathlib import Path

from tools.v33_forward_net_paper import paper_pnl
from tools.v33_copyability_validation import _validate_paper

SOURCE = Path(os.getenv("V33_COMPARE_SOURCE_DIR", "v33-snapshot"))
CHECKPOINT = Path("research/v33-shadow-outcome-checkpoint.json")
OUTPUT = Path("artifacts/v33-rejected-vs-sent-paper.json")
MIN_OBSERVED = 30
SCENARIOS = (
    ("no_fee", 0.0, 1.0),
    ("fee_5pct", 0.05, 1.0),
    ("fee_7pct", 0.07, 1.0),
    ("fee_5pct_and_5pct_worse_ask", 0.05, 1.05),
)


def finite_number(obj, min_value=None):
    if isinstance(obj, bool) or not isinstance(obj, (int,float)):
        return None
    x = float(obj)
    if not math.isfinite(x) or (min_value is not None and x <= min_value):
        return None
    return x


def archived_rejected(row):
    if not isinstance(row, dict) or row.get("shadow_decision") != "blocked_recent":
        return None, "not_blocked_recent"
    if row.get("result") not in ("win", "loss"):
        return None, "outcome_not_final"
    proof = row.get("original_book_quote")
    if not isinstance(proof, dict) or proof.get("no_fill_assumed") is not True:
        return None, "original_quote_unavailable"
    if proof.get("topic") != "sports":
        return None, "non_sports_proof"
    ask = finite_number(proof.get("hypothetical_copy_ask"), 0)
    stake = finite_number(proof.get("hypothetical_copy_stake_usd"), 0)
    qty = finite_number(proof.get("hypothetical_copy_shares"), 0)
    if ask is None or not ask < 1 or stake is None or not stake <= 100 or qty is None:
        return None, "malformed_original_quote"
    if not math.isclose(ask*qty, stake, rel_tol=0.025, abs_tol=.025):
        return None, "quote_depth_stake_mismatch"
    try:
        quoted = int(proof.get("book_quoted_at"))
        source_at = int(proof.get("source_trade_at"))
        settled = int(row.get("settled_at"))
    except (TypeError, ValueError, OverflowError):
        return None, "missing_chronology"
    if not (0 < source_at <= quoted < settled):
        return None, "not_verifiably_before_settlement"
    if quoted-source_at > 300:
        return None, "source_to_quote_lag_over_five_minutes"
    condition = row.get("condition")
    if not isinstance(condition, str) or len(condition) != 66:
        return None, "invalid_market_identity"
    return {
        "market": condition.lower(),
        "cohort": "blocked_recent",
        "result": row["result"],
        "ask": ask,
        "stake": stake,
        "quoted_at": quoted,
        "source_trade_to_quote_seconds": quoted-source_at,
        "outcome_evidence": "inherited_v2_classification",
    }, None


def sent_row(row):
    if not isinstance(row, dict):
        return None, "bad_sent_row"
    if row.get("stream") != "telegram_sent":
        return None, "not_sent"
    _, reason = _validate_paper(row)
    if reason:
        return None, reason
    condition = row.get("condition")
    if not isinstance(condition, str) or len(condition) != 66:
        return None, "invalid_market_identity"
    return {
        "market": condition.lower(),
        "cohort": "telegram_sent",
        "result": row["final_outcome"],
        "ask": float(row["ask_vwap"]),
        "stake": float(row["stake_usd"]),
        "quoted_at": int(row["book_quoted_at"]),
        "source_trade_to_quote_seconds": None,
        "outcome_evidence": "inherited_v2_classification",
    }, None


def group_summary(rows):
    stake = sum(x["stake"] for x in rows)
    scenarios = {}
    for key, fee, deterioration in SCENARIOS:
        valued = []
        for row in rows:
            value=paper_pnl(row["stake"],row["ask"],row["result"]=="win",
                            fee,deterioration)
            if value is not None:
                valued.append((row["stake"], value))
        amount = sum(x[0] for x in valued)
        pnl = sum(x[1] for x in valued)
        scenarios[key]={
            "priced_unique_conditions":len(valued),
            "missing_unpriceable_scenarios":len(rows)-len(valued),
            "model_stake_usd":round(amount,3),
            "model_hold_pnl_usd":round(pnl,5) if amount else None,
            "model_hold_roi":round(pnl/amount,6) if amount else None,
            "actual_filled_roi":None,
        }
    return {
        "observed_unique_market_conditions":len(rows),
        "wins":sum(x["result"]=="win" for x in rows),
        "losses":sum(x["result"]=="loss" for x in rows),
        "observed_book_stake_usd":round(stake,3),
        "source_to_quote_latency_seconds_median": (
            (lambda ts:(ts[len(ts)//2] if len(ts)%2 else
                        (ts[len(ts)//2-1]+ts[len(ts)//2])/2))(
                            sorted(x["source_trade_to_quote_seconds"] for x in rows
                                   if x["source_trade_to_quote_seconds"] is not None))
            if any(x["source_trade_to_quote_seconds"] is not None for x in rows)
            else None
        ),
        "descriptive_sample_30plus_market_conditions":len(rows) >= MIN_OBSERVED,
        "sports_event_independence_verified":False,
        "actual_user_fills":0,
        "actual_copy_roi":None,
        "scenario_models":scenarios,
    }


def build_report(checkpoint, forward):
    if not isinstance(checkpoint, dict) or checkpoint.get("schema") != (
            "v33_durable_shadow_checkpoint_1"):
        raise ValueError("Unknown durable checkpoint")
    if not isinstance(forward, dict) or forward.get("schema") != (
            "v33_forward_net_paper_1"):
        raise ValueError("Unknown forward book ledger")
    if forward.get("forward_only_no_backfilled_quotes") is not True:
        raise ValueError("Only prospectively observed quotes qualify")
    considered = []
    rejected = collections.Counter()
    for row in checkpoint.get("rows") or []:
        candidate, reason = archived_rejected(row)
        if candidate is None:
            rejected["blocked_"+reason] += 1
        else:
            considered.append(candidate)
    for row in forward.get("positions") or []:
        candidate,reason = sent_row(row)
        if candidate is None:
            rejected["sent_"+reason] += 1
        else:
            considered.append(candidate)
    # Market-level condition is not a unique sports event. Exclude all
    # condition collisions across BOTH groups so no contradictory scoring.
    groups=collections.defaultdict(list)
    for x in considered:
        groups[x["market"]].append(x)
    duplicated=sum(len(r) for r in groups.values() if len(r)>1)
    unique=[v[0] for v in groups.values() if len(v)==1]
    by={cohort:group_summary([x for x in unique if x["cohort"]==cohort])
        for cohort in ("blocked_recent","telegram_sent")}
    sample_enough=all(
        v["descriptive_sample_30plus_market_conditions"] for v in by.values())
    return {
        "schema":"v33_archived_quote_two_cohort_paper_1",
        "checkpoint_completed_runs":(checkpoint.get("meta") or {}).get("completed_runs"),
        "checkpoint_rows":len(checkpoint.get("rows") or []),
        "forward_paper_rows":len(forward.get("positions") or []),
        "confirmed_quote_candidate_rows":len(considered),
        "market_condition_duplicates_excluded":duplicated,
        "exclusion_reasons":dict(rejected),
        "by_cohort":by,
        "both_descriptive_samples_reach_30":sample_enough,
        "data_is_not_matched_by_event_or_time":True,
        "market_condition_is_not_independent_sport_event":True,
        "survivorship_and_settlement_censoring_present":True,
        "causal_value_of_recent_form_gate":None,
        "actual_copy_trading_roi":None,
        "approve_live_wallets_or_lower_risk_thresholds":False,
        "note":("Book quote sampled while signal active, held hypothetically to "
                "independently classified v2 payout. Fees are modeled, "
                "not charged. No actual order fills, cashouts, or full "
                "counterfactual cohort construction."),
    }


def main():
    checkpoint=json.loads(CHECKPOINT.read_text())
    forward=json.loads((SOURCE/"research/v33_forward_net_paper.json").read_text())
    report=build_report(checkpoint,forward)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
    print("V33 ARCHIVED REJECTED VS SENT PAPER",json.dumps({
        k:v for k,v in report.items() if k not in ("note",)},sort_keys=True))


if __name__=="__main__":
    main()
