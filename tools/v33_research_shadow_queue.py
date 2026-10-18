#!/usr/bin/env python3
"""Research-only v3.3 wallet audit queue. NEVER a signal whitelist.

Joins independent, imperfect snapshots without imputing missing PnL or fills.
Order means "which wallets deserve further verification", NOT copy priority.
The active 300-wallet monitoring list and its state are NEVER changed.
"""
import collections
import json
import math
import os
import time
from pathlib import Path

INPUT_ROOT = Path(os.getenv("V33_SHADOW_INPUT_DIR", "."))
INPUT_REF = os.getenv("V33_SHADOW_INPUT_REF", "audit_branch_snapshot")
LEGACY = INPUT_ROOT / "research/v33_wallet_shortlist.json"
EVIDENCE = INPUT_ROOT / "research/v33_evidence_rank.json"
COHORT = INPUT_ROOT / "research/v33_wallet_copy_cohort.json"
FORWARD = INPUT_ROOT / "research/v33_forward_net_paper.json"
RUNTIME = INPUT_ROOT / "research/v33_signal_state.json"
RISK = Path("artifacts/v33-risk-api-audit.json")
OUTPUT = Path("artifacts/v33-shadow-wallet-queue.json")
MAX_QUEUE = 300
MIN_FORWARD_OBSERVATIONS = 30
MAX_SOURCE_AGE_DAYS = 7
RISK_SEVERE = {"severe_unredeemed_loss_inventory", "multiple_major_unredeemed_losses"}


def load(path, required=True):
    if not path.exists():
        if required:
            raise FileNotFoundError(str(path))
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def wallet_id(value):
    if not isinstance(value, str):
        return None
    value = value.lower()
    if len(value) == 42 and value.startswith("0x") and all(
            c in "0123456789abcdef" for c in value[2:]):
        return value
    return None


def valid_baseline(row):
    """Approximate snapshot shortlist selection; not the running workflow."""
    def numeric(key):
        v = row.get(key)
        if isinstance(v, bool):
            return float("-inf")
        try:
            num = float(v)
        except (TypeError, ValueError, OverflowError):
            return float("-inf")
        return num if math.isfinite(num) else float("-inf")
    return (numeric("closed") >= 30
            and numeric("profitable_closed_rate") >= .70
            and numeric("realized_closed_pnl") >= 100
            and numeric("profit_factor") >= 1.15)


def observed_risk_pages(risk):
    """Match partial wallet prefixes only when the universe match is unique."""
    return risk.get("samples") or [] if isinstance(risk, dict) else []


def forward_summary(rows):
    """Count only adjudicated book-quote paper observations; no real fills."""
    result = collections.defaultdict(list)
    for x in rows or []:
        if not isinstance(x, dict) or x.get("status") != "verified_settled_paper":
            continue
        wallet = wallet_id(x.get("wallet"))
        if not wallet or x.get("final_outcome") not in ("win", "loss"):
            continue
        if x.get("real_trading_fill") is not False:
            continue
        if x.get("stream") not in ("telegram_sent", "shadow_only"):
            continue
        pnl = (x.get("hypothetical_pnl_usd") or {}).get(
            "pnl_fee_0.05_plus_5pct_worse_entry")
        stake = x.get("stake_usd")
        if not isinstance(pnl, (int,float)) or isinstance(pnl, bool):
            continue
        if not isinstance(stake, (int,float)) or isinstance(stake, bool):
            continue
        if not math.isfinite(pnl) or not math.isfinite(stake) or not 0 < stake <= 100:
            continue
        result[wallet].append(x)
    out = {}
    for wallet, items in result.items():
        # A single condition may have multiple fills/outcomes. Treat duplicate
        # condition observations as ambiguous rather than repeat profits.
        groups = collections.defaultdict(list)
        for x in items:
            condition = str(x.get("condition") or "")
            if condition:
                groups[condition].append(x)
        singles = [v[0] for v in groups.values() if len(v) == 1]
        total = sum(x["stake_usd"] for x in singles)
        pnl = sum(x["hypothetical_pnl_usd"][
            "pnl_fee_0.05_plus_5pct_worse_entry"] for x in singles)
        out[wallet] = {
            "distinct_quoted_conditions_settled": len(singles),
            "ambiguous_same_condition_observations": sum(
                len(v) for v in groups.values() if len(v) > 1),
            "sample_stake_usd": round(total, 3),
            "fee_and_price_stress_model_pnl_usd": round(pnl, 3),
            "fee_and_price_stress_model_roi": round(pnl/total, 5) if total else None,
            "distinct_sport_events_verified": False,
            "actual_filled_trades": 0,
            "actual_net_copy_roi": None,
            "fee_model_not_actual_fee_receipts": True,
        }
    return out


def build_report(legacy, evidence, cohort, forward, risk=None, limit=MAX_QUEUE,
                 runtime=None, observed_at=None):
    if legacy.get("schema") != "v33_wallet_shortlist_1":
        raise ValueError("Invalid legacy shortlist schema")
    if evidence.get("schema") != "v33_evidence_rank_1":
        raise ValueError("Invalid sports evidence schema")
    if cohort.get("schema") != "v33_wallet_copy_cohort_1":
        raise ValueError("Invalid lag-copy study schema")
    if forward.get("schema") != "v33_forward_net_paper_1":
        raise ValueError("Invalid forward paper schema")
    if not (1 <= limit <= MAX_QUEUE):
        raise ValueError("Invalid queue limit")
    original = legacy.get("shortlist")
    audited = evidence.get("ranked_for_research_only")
    if not isinstance(original, list) or not isinstance(audited, list):
        raise ValueError("Missing source wallet lists")
    legacy_rows = {}
    baseline = []
    for row in original:
        if not isinstance(row, dict):
            continue
        wallet = wallet_id(row.get("wallet"))
        if wallet and wallet not in legacy_rows:
            legacy_rows[wallet] = row
            if valid_baseline(row) and len(baseline) < MAX_QUEUE:
                baseline.append(wallet)
    evidence_rows = {
        w: row for row in audited if isinstance(row, dict)
        if (w := wallet_id(row.get("wallet")))
    }
    cohort_rows = {
        w: row for row in cohort.get("wallets", []) if isinstance(row, dict)
        if (w := wallet_id(row.get("wallet")))
    }
    if forward.get("forward_only_no_backfilled_quotes") is False:
        # Exclude potentially retrospectively reconstructed quotes from
        # prospective paper evidence; never assume they were tradable.
        paper = {}
    else:
        paper = forward_summary(forward.get("positions"))
    observed = {}
    all_wallets = sorted(set(legacy_rows) | set(evidence_rows) | set(cohort_rows))
    for item in observed_risk_pages(risk):
        if not isinstance(item, dict):
            continue
        prefix = item.get("wallet_prefix")
        if not isinstance(prefix, str) or len(prefix) < 12:
            continue
        matches = [w for w in all_wallets if w.startswith(prefix.lower())]
        if len(matches) == 1:
            observed[matches[0]] = item

    rows = []
    for wallet in all_wallets:
        l = legacy_rows.get(wallet, {})
        e = evidence_rows.get(wallet, {})
        c = cohort_rows.get(wallet, {})
        f = paper.get(wallet, {})
        rv = observed.get(wallet)
        blockers = list(dict.fromkeys(
            [str(v) for v in e.get("blocking_reasons", [])]
            + [str(v) for v in c.get("historical_data_warnings", [])]))
        if not e:
            blockers.append("no_independent_sports_event_audit")
        if not c:
            blockers.append("no_independent_lag_price_proxy_study")
        if not f or f.get("distinct_quoted_conditions_settled", 0) < MIN_FORWARD_OBSERVATIONS:
            blockers.append("insufficient_forward_book_quoted_paper_outcomes")
        if rv:
            for page in ("CLOSED", "REDEEMABLE_LOST"):
                observed_page = (rv.get("pages") or {}).get(page) or {}
                if not observed_page.get("pagination_complete"):
                    blockers.append("partial_v2_" + page.lower())
                if observed_page.get("api_or_schema_error"):
                    blockers.append("v2_api_or_schema_unknown")
                if any(v != "valid" for v in (
                        observed_page.get("timestamp_states") or {})):
                    blockers.append("v2_position_activity_timestamp_unknown")
        else:
            blockers.append("no_recent_v2_risk_page_audit")
        known_severe = bool(set(blockers) & RISK_SEVERE)
        if rv:
            lost = (rv.get("pages") or {}).get("REDEEMABLE_LOST") or {}
            if lost.get("redeemable_major_loss_rows", 0) >= 4:
                known_severe = True
                blockers.append("observed_four_plus_major_redeemable_losses")
        ready_history = bool(e.get("history_evidence_complete"))
        price_study_valid = bool(c.get("copy_roi_verified"))  # always false in current data
        enough_forward = bool(
            f.get("distinct_quoted_conditions_settled", 0) >= MIN_FORWARD_OBSERVATIONS)
        # No real fills, fees or unbiased out-of-sample estimate: no live approval.
        readiness = ("RISK_REVIEW" if known_severe else
                     "HISTORY_REVIEW" if not ready_history else
                     "EXECUTION_REVIEW")
        coverage = int(e.get("distinct_verified_sports_events") or 0)
        # Prioritize correction of bad/unknown histories, not high historical
        # win rates, raw PnL or cherry-picked positive copy proxy performance.
        priority = (
            0 if known_severe else 1 if rv else 2 if e else 3,
            -int(bool(rv)),
            -min(coverage, 30),
            -int(bool(f)),
            -int(wallet in baseline),
            wallet)
        rows.append({
            "wallet": wallet,
            "legacy_snapshot_top_300": wallet in baseline,
            "legacy_closed_sample": l.get("closed"),
            "legacy_winrate_not_predictive": l.get("profitable_closed_rate"),
            "legacy_profit_factor_not_predictive": l.get("profit_factor"),
            "sports_verified_unique_events_observed": coverage,
            "sports_by_league": e.get("by_league") or {},
            "sports_history_complete": ready_history,
            "sports_evidence_status": e.get("status", "NOT_AUDITED"),
            "observed_loss_inventory_major": (rv or {}).get("pages", {})
                .get("REDEEMABLE_LOST", {}).get("redeemable_major_loss_rows"),
            "recent_risk_page_audited": bool(rv),
            "recent_risk_page_is_full_ledger": False,
            "historical_price_proxy": c.get("status", "NOT_EVALUATED"),
            "historical_copy_proxy_not_executable_roi": (
                c.get("roi_if_5pct_worse_entry_5m_no_fees")),
            "forward_book_quote_paper": f if f else None,
            "research_status": readiness,
            "historical_copy_roi_verified": price_study_valid,
            "forward_model_sample_sufficient": enough_forward,
            "net_follower_roi_verified": False,
            "live_approved": False,
            "blocking_reasons": list(dict.fromkeys(blockers)),
            "_audit_priority": priority,
        })
    rows.sort(key=lambda x: x["_audit_priority"])
    for row in rows:
        del row["_audit_priority"]
    chosen = rows[:limit]
    baseline_set = set(baseline)
    chosen_set = {x["wallet"] for x in chosen}
    old_in_audits = sum(w in evidence_rows for w in baseline)
    last_saved = set()
    last_saved_source = False
    if isinstance(runtime, dict) and runtime.get("version") == 33:
        cursors = runtime.get("cursors")
        last_run = runtime.get("last_run") or {}
        if (isinstance(cursors, dict) and cursors
                and len(cursors) == last_run.get("watching")):
            last_saved = {w for address in cursors if (w := wallet_id(address))}
            last_saved_source = bool(last_saved)
    input_times = [
        v for v in (legacy.get("observed_at"),
                    evidence.get("source_updated_at"),
                    cohort.get("input_truth_updated_at"),
                    forward.get("official_source_updated_at"))
        if isinstance(v, int) and v > 0]
    input_spread = max(input_times)-min(input_times) if input_times else None
    now = int(observed_at if observed_at is not None else time.time())
    stale = {name: (now-ts)//86400 > MAX_SOURCE_AGE_DAYS
             for name, ts in (
                 ("legacy_shortlist", legacy.get("observed_at")),
                 ("sports_truth_evidence", evidence.get("source_updated_at")),
                 ("copy_proxy_cohort", cohort.get("input_truth_updated_at")),
                 ("forward_paper_truth", forward.get("official_source_updated_at")))
             if isinstance(ts, int) and ts > 0}
    return {
        "schema": "v33_research_shadow_queue_1",
        "purpose": "research_priority_not_trade_signal",
        "input_ref": INPUT_REF,
        "generated_at": now,
        "source_snapshot_spread_seconds": input_spread,
        "older_than_seven_days": stale,
        "max_research_slots": limit,
        "source_timestamps": {
            "legacy_shortlist": legacy.get("observed_at"),
            "sports_truth_evidence": evidence.get("source_updated_at"),
            "copy_proxy_cohort": cohort.get("input_truth_updated_at"),
            "forward_paper_truth": forward.get("official_source_updated_at"),
            "optional_risk_page_audit": (risk or {}).get("captured_at"),
        },
        "research_population": len(all_wallets),
        "research_queue_count": len(chosen),
        "snapshot_legacy_baseline_count": len(baseline),
        "baseline_is_exact_live_inventory": False,
        "baseline_not_live_explanation": "Snapshot sorted eligibility, not runtime priority, quarantine or live state.",
        "baseline_with_sports_audit": old_in_audits,
        "queue_overlap_with_snapshot_legacy": len(chosen_set & baseline_set),
        "last_saved_runtime_cursors_available": last_saved_source,
        "last_saved_runtime_wallets_count": len(last_saved),
        "queue_overlap_with_last_saved_runtime": (
            len(chosen_set & last_saved) if last_saved_source else None),
        "runtime_cursor_snapshot_is_current_inventory": False,
        "status_counts": dict(collections.Counter(x["research_status"] for x in chosen)),
        "historical_audit_ready_in_queue": sum(x["sports_history_complete"] for x in chosen),
        "forward_model_30plus_conditions_in_queue": sum(x["forward_model_sample_sufficient"] for x in chosen),
        "no_real_fills_confirmed": True,
        "live_approved_count": 0,
        "candidate_rows": chosen,
        "warnings": [
            "Research order is a validation queue, never best traders or buy whitelist.",
            "Missing results are UNKNOWN, not zero losses or profitable trades.",
            "Legacy winrate samples CLOSED positions, ignores other economic paths.",
            "Positive historical price proxies lack executable asks, fees and independent outcomes.",
            "Forward paper quotes are not actual fills; several conditions can share a sporting event.",
            "Risk first-page samples can be incomplete even with pagination.has_more=false; no reconciled ledger.",
            "Inputs originate from different snapshots and dates; this is not a matched-time backtest.",
            "The last saved runtime cursor set is a historical monitor snapshot, not verified real-time inventory.",
        ],
    }


def main():
    report = build_report(load(LEGACY), load(EVIDENCE), load(COHORT),
                          load(FORWARD), load(RISK, required=False),
                          runtime=load(RUNTIME, required=False))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False,
                                indent=2, allow_nan=False) + "\n", encoding="utf-8")
    compact = {key: value for key,value in report.items()
               if key not in ("candidate_rows", "warnings")}
    print("V33 RESEARCH SHADOW QUEUE", json.dumps(compact, sort_keys=True))
    print("NO PRODUCTION CHANGE; NO REAL ORDERS; NO TELEGRAM; ZERO LIVE APPROVALS")


if __name__ == "__main__":
    main()
