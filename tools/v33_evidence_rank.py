#!/usr/bin/env python3
"""Evidence-based SPORT wallet report; deliberately NOT a live signal filter.

Reads verified v3.3 outcome audits. Historical realized PnL, profitable exit
rate, and crypto/politics performance NEVER increase the research score.
No real trading. No Telegram. No retrospective use of post-result ask prices.
"""
import collections
import json
import math
from pathlib import Path

SOURCE = Path("research/v33_truth_report.json")
OUTPUT = Path("research/v33_evidence_rank.json")
MIN_EVENTS_FOR_HISTORY = 30
MIN_PAPER_FOR_EVALUATION = 30


def wilson_lower(wins, n, z=1.96):
    if not n:
        return None
    p = wins / n
    denom = 1 + z*z/n
    return (p + z*z/(2*n) - z*math.sqrt(
        (p*(1-p) + z*z/(4*n))/n)) / denom


def unique_sports_events(events):
    """Drop ambiguous or multiply-traded sporting events.

    The same event may have many position markets. One event slug counts
    once ONLY if exactly one verified condition has a confirmed direction.
    Repeated bets in the same event are marked ambiguous, not more wins.
    """
    by_slug = collections.defaultdict(list)
    missing = 0
    for row in events or []:
        if not isinstance(row, dict):
            continue
        league = str(row.get("league") or "")
        slug = str(row.get("event_slug") or "").strip().lower()
        outcome = row.get("direction")
        if league in ("", "UNVERIFIED") or outcome not in ("win", "loss"):
            continue
        if not slug:
            missing += 1
            continue
        by_slug[slug].append(row)
    unambiguous = []
    duplicate = 0
    for slug, group in by_slug.items():
        conditions = {item.get("condition") for item in group}
        if len(group) != 1 or len(conditions) != 1:
            duplicate += 1
            continue
        unambiguous.append(group[0])
    unambiguous.sort(key=lambda x: -int(x.get("resolved_at") or 0))
    return unambiguous, {"events_missing_slug": missing,
                         "multiple_markets_per_event": duplicate}


def wallet_evidence(wallet, data, alert_rows):
    position = data.get("position_truth") or {}
    events, ambiguities = unique_sports_events(position.get("events"))
    league_stats = collections.defaultdict(lambda: {"wins": 0, "losses": 0,
                                                     "priced_events": 0,
                                                     "source_first_buy_hold_pnl_per_1usd": 0.0,
                                                     "source_implied_wins": 0.0})
    for e in events:
        league = str(e["league"]).upper()
        bucket = league_stats[league]
        bucket["wins" if e["direction"] == "win" else "losses"] += 1
        price = e.get("source_first_buy_price")
        try:
            p = float(price) if price is not None else None
        except (TypeError, ValueError):
            p = None
        if p is not None and math.isfinite(p) and 0 < p < 1:
            bucket["priced_events"] += 1
            bucket["source_implied_wins"] += p
            bucket["source_first_buy_hold_pnl_per_1usd"] += (
                (1 / p - 1) if e["direction"] == "win" else -1)
    leagues = {}
    for league, s in league_stats.items():
        n = s["wins"] + s["losses"]
        price_count = s["priced_events"]
        leagues[league] = {
            "verified_unique_event_observations": n,
            "wins": s["wins"], "losses": s["losses"],
            "observed_accuracy": round(s["wins"]/n, 4),
            "wilson_95pct_lower": round(wilson_lower(s["wins"], n), 4),
            "source_first_buy_price_covered": price_count,
            "source_first_buy_implied_wins": round(s["source_implied_wins"], 3),
            "source_first_buy_hold_roi_no_fees": round(
                s["source_first_buy_hold_pnl_per_1usd"]/price_count, 4)
                if price_count == n and n else None,
            "not_copy_roi": True,
        }
    scoped_alerts = [a for a in (alert_rows or [])
                     if isinstance(a, dict) and a.get("wallet") == wallet
                     and isinstance(a.get("paper_hold_pnl_no_fees"), (int,float))
                     and isinstance(a.get("paper_stake_usd"), (int,float))
                     and 0 < a["paper_stake_usd"] <= 100
                     and a.get("result") in ("win", "loss")]
    paper_stake = sum(x["paper_stake_usd"] for x in scoped_alerts)
    paper_pnl = sum(x["paper_hold_pnl_no_fees"] for x in scoped_alerts)
    unknowns = data.get("unverified_final_or_mapping_count")
    if unknowns is None:
        unknowns = sum(v for k,v in (data.get("status_counts") or {}).items()
                       if k not in ("win","loss","push_or_partial",
                                    "unknown_resolution_pending","not_verified_sport"))
    accounting = position.get("position_accounting") or {}
    alerts_n = len(scoped_alerts)
    reasons = []
    if not data.get("trade_window_complete"):
        reasons.append("incomplete_maker_taker_trade_window")
    if not data.get("market_scan_complete"):
        reasons.append("truncated_markets")
    if not position.get("position_history_complete"):
        reasons.append("incomplete_closed_redeemable_positions")
    if not position.get("position_market_scan_complete"):
        reasons.append("truncated_position_market_scan")
    if not data.get("certified_sample") or not position.get("certified_sample"):
        reasons.append("sports_outcomes_not_certified")
    if unknowns:
        reasons.append("unverified_final_outcomes")
    if ambiguities["events_missing_slug"] or ambiguities["multiple_markets_per_event"]:
        reasons.append("event_deduplication_ambiguity")
    if len(events) < MIN_EVENTS_FOR_HISTORY:
        reasons.append("fewer_than_30_distinct_verified_sports_events")
    # Severe unredeemed losses are a reason for manual review, not a winrate.
    if accounting.get("unredeemed_major_losers_100usd", 0) >= 4:
        reasons.append("severe_unredeemed_loss_inventory")
    if alerts_n < MIN_PAPER_FOR_EVALUATION:
        reasons.append("fewer_than_30_forward_book_quoted_paper_outcomes")
    # Fees are not included and no real fill can be assumed. Even >=30
    # profitable paper holds are research evidence, never live authorization.
    if paper_stake > 0 and paper_pnl <= 0:
        reasons.append("nonpositive_paper_hold_before_fees")
    ready_history = not any(x in reasons for x in (
        "incomplete_maker_taker_trade_window", "truncated_markets",
        "incomplete_closed_redeemable_positions",
        "truncated_position_market_scan", "sports_outcomes_not_certified",
        "unverified_final_outcomes", "event_deduplication_ambiguity",
        "fewer_than_30_distinct_verified_sports_events",
        "severe_unredeemed_loss_inventory"))
    ready_forward = (alerts_n >= MIN_PAPER_FOR_EVALUATION and paper_stake > 0
                     and paper_pnl > 0)
    return {
        "wallet": wallet, "audit_at": data.get("audited_at"),
        "observed_resolved_market_outcomes": data.get("resolved_sports_sample", 0),
        "distinct_verified_sports_events": len(events),
        "by_league": leagues, "ambiguities": ambiguities,
        "history_evidence_complete": bool(ready_history),
        "forward_paper_settled": alerts_n,
        "forward_paper_hold_stake_usd": round(paper_stake, 2),
        "forward_paper_hold_pnl_no_fees": round(paper_pnl, 2),
        "forward_paper_hold_roi_no_fees": round(paper_pnl/paper_stake,4)
             if paper_stake > 0 else None,
        "forward_paper_evidence_sufficient": bool(ready_forward),
        "copy_roi_verified": False,  # no order/fill/exit/fees records
        "live_ranking_approved": False,
        "status": "HISTORICAL_AUDIT_READY" if ready_history else
                  ("OBSERVATIONS_ONLY" if len(events) >= 5 else "INSUFFICIENT"),
        "blocking_reasons": reasons,
    }


def build_report(source):
    wallets = source.get("wallets") or {}
    alerts = (source.get("alerts") or {}).get("rows") or []
    entries = [wallet_evidence(addr,data,alerts) for addr,data in wallets.items()
               if isinstance(data,dict)]
    entries.sort(key=lambda x: (
        x["status"] != "HISTORICAL_AUDIT_READY",
        x["status"] != "OBSERVATIONS_ONLY",
        -x["distinct_verified_sports_events"],
        -max((v["wilson_95pct_lower"] for v in x["by_league"].values()),
             default=0),
        x["wallet"]))
    return {
        "schema": "v33_evidence_rank_1", "source_updated_at": source.get("updated_at"),
        "n_wallets_audited": len(entries),
        "n_historical_audit_ready": sum(x["history_evidence_complete"] for x in entries),
        "n_forward_paper_evaluable": sum(x["forward_paper_evidence_sufficient"] for x in entries),
        "n_live_approved": 0,
        "ranked_for_research_only": entries,
        "disclaimer": "Not buy recommendations. Historically profitable closed "
            "positions are not sports outcome accuracy. Forward paper holding "
            "ignores fees and execution; no actual copy ROI is known. A live "
            "ranking must be separately validated out-of-sample before activation."
    }


def main():
    obj = json.loads(SOURCE.read_text())
    report = build_report(obj)
    OUTPUT.write_text(json.dumps(report, separators=(",", ":"), ensure_ascii=False)+"\n")
    print("V33 EVIDENCE RANK", json.dumps({
        "wallets": report["n_wallets_audited"],
        "historical_ready": report["n_historical_audit_ready"],
        "forward_paper_evaluable": report["n_forward_paper_evaluable"],
        "live_approved": report["n_live_approved"]}))


if __name__ == "__main__":
    main()
