#!/usr/bin/env python3
"""Research-only broad sports-wallet observation queue.

Uses the complete saved 12k-style universe, not the 350-person shortlist.
No Polymarket requests, no Telegram, no orders, no changes to live watch list.
Activity timestamps reflect sampled public trades, NOT independently audited
trading skill, complete wallet history, or executable follower edge.
"""
import collections
import json
import math
import os
import time
from pathlib import Path

ROOT = Path(os.getenv("V33_BROAD_INPUT_DIR", "v33-snapshot"))
OUTPUT = Path("artifacts/v33-broad-sports-watch-research.json")
TARGET = 1200
WINDOWS = (24*3600, 48*3600, 7*86400)


def valid_wallet(key):
    return (isinstance(key, str) and len(key) == 42 and key[:2] == "0x"
            and all(c in "0123456789abcdef" for c in key[2:].lower()))


def valid_time(value, now):
    if isinstance(value, bool):
        return None
    try:
        v = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if 0 < v <= now+120 else None


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def historical_score(item, now):
    """Legacy CLOSED-only pass is NOT complete economics or live approval."""
    t = valid_time(item.get("score_checked_at"), now)
    pf = numeric(item.get("profit_factor"))
    n = numeric(item.get("n"))
    win = numeric(item.get("winrate"))
    pnl = numeric(item.get("recent_realized_pnl"))
    if not t or now-t > 7*86400:
        return "UNVERIFIED_OR_EXPIRED"
    if any(v is None for v in (pf,n,win,pnl)):
        return "INCOMPLETE"
    return ("LEGACY_CLOSED_SNAPSHOT_PASS" if n >= 30 and win >= .70 and
            pnl >= 100 and pf >= 1.15 else "LEGACY_CLOSED_SNAPSHOT_FAIL")


def build_report(universe, shortlist, runtime, now=None, limit=TARGET):
    now = int(time.time()) if now is None else int(now)
    if not isinstance(universe, dict) or universe.get("schema") != "v33_wallet_universe_1":
        raise ValueError("Unexpected source universe schema")
    wallets = universe.get("wallets")
    if not isinstance(wallets, dict):
        raise ValueError("Malformed universe wallet map")
    if not isinstance(shortlist, dict) or shortlist.get("schema") != "v33_wallet_shortlist_1":
        raise ValueError("Unexpected shortlist schema")
    if not isinstance(runtime, dict) or runtime.get("version") != 33:
        raise ValueError("Unexpected read-only runtime state")
    if not 1 <= limit <= TARGET:
        raise ValueError("Invalid shadow research limit")
    current_set = {k.lower() for k in (runtime.get("cursors") or {})
                   if valid_wallet(k)}
    old_shortlist = {str(x.get("wallet")).lower()
                     for x in shortlist.get("shortlist", [])
                     if isinstance(x,dict) and valid_wallet(x.get("wallet"))}
    scored = collections.Counter()
    age_counts = collections.Counter()
    candidates = []
    malformed = 0
    for key, item in wallets.items():
        if not valid_wallet(key) or not isinstance(item,dict):
            malformed += 1
            continue
        wallet = key.lower()
        sport_at = valid_time(item.get("sports_last_buy"), now)
        if sport_at is None:
            continue
        seconds = now-sport_at
        if seconds > WINDOWS[-1]:
            continue
        age = ("observed_sports_buy_24h" if seconds <= WINDOWS[0]
               else "observed_sports_buy_48h" if seconds <= WINDOWS[1]
               else "observed_sports_buy_7d")
        age_counts[age] += 1
        legacy = historical_score(item, now)
        scored[legacy] += 1
        mid = max(0,int(numeric(item.get("mid_buys_sampled")) or 0))
        total = max(0,int(numeric(item.get("buy_rows_sampled")) or 0))
        candidates.append({
            "wallet": wallet,
            "last_sampled_sports_buy_at": sport_at,
            "observed_sports_activity": age,
            "mid_price_buy_rows_sampled_nonunique": mid,
            "all_buy_rows_sampled_nonunique": total,
            "legacy_closed_score_status": legacy,
            "in_current_300_cursor_snapshot": wallet in current_set,
            "in_old_350_shortlist": wallet in old_shortlist,
            "independent_complete_wallet_economics": False,
            "independent_event_skill_verified": False,
            "executable_net_follower_roi_verified": False,
            "risk_page_verified": False,
            "live_approved": False,
        })
    # Ex-ante activity recency and sample price observations are research
    # priorities ONLY; historical winrate/PF never grants approval or rank.
    candidates.sort(key=lambda x: (
        -x["last_sampled_sports_buy_at"],
        -min(x["mid_price_buy_rows_sampled_nonunique"],20),
        -min(x["all_buy_rows_sampled_nonunique"],100),x["wallet"]))
    queue = candidates[:limit]
    select = {x["wallet"] for x in queue}
    missing = max(0,limit-len(queue))
    return {
        "schema": "v33_broad_sports_research_1",
        "as_of":now,
        "input_universe_wallets":len(wallets),
        "source_shortlist_wallets":len(old_shortlist),
        "source_runtime_cursor_wallets":len(current_set),
        "source_runtime_cursors_are_exact_current_inventory":False,
        "sampled_sports_buy_24h_total":age_counts["observed_sports_buy_24h"],
        "sampled_sports_buy_48h_total":sum(
            age_counts[k] for k in ("observed_sports_buy_24h","observed_sports_buy_48h")),
        "sampled_sports_buy_7d_total":len(candidates),
        "historical_closed_score_status_for_sports_7d":dict(scored),
        "invalid_wallet_records":malformed,
        "research_queue_target":limit,
        "research_queue_count":len(queue),
        "research_queue_unfilled_slots":missing,
        "research_queue_activity_breakdown":dict(collections.Counter(
            x["observed_sports_activity"] for x in queue)),
        "queue_overlap_current_runtime_cursor_snapshot":len(select & current_set),
        "queue_not_in_current_runtime_cursor_snapshot":len(select-current_set),
        "queue_overlap_350_shortlist":len(select & old_shortlist),
        "newly_explorable_sports_wallets_vs_runtime":len(select-current_set),
        "cost_projection_wallet_polls_per_full_cycle": {
            "current_300":300,"research_600":600,"research_1000":1000,
            "research_1200":1200,
            "extra_over_current_if_1000":700,
            "extra_over_current_if_1200":900,
            "not_an_api_rate_or_latency_benchmark":True,
        },
        "wallets_live_approved":0,
        "safe_for_automatic_telegram_expansion":False,
        "not_a_reconciled_ledger":True,
        "not_a_profitability_ranking":True,
        "research_queue":queue,
        "notes":[
            "Observed sports timestamps are from repeated sampled market trade pages.",
            "Samples are not complete activity history; missing/future stamps are omitted.",
            "Historical closed score can miss settled but unredeemed losing positions.",
            "No wallet from this output may bypass real-time book, risk or market checks.",
            "Increasing live scans from 300 to 1000 may require 700 extra wallet polls per cycle.",
            "Production trading, Telegram, scheduled monitoring and wallet cursors unchanged.",
        ],
    }


def main():
    univ=json.loads((ROOT/"research/v33_wallet_universe.json").read_text())
    short=json.loads((ROOT/"research/v33_wallet_shortlist.json").read_text())
    state=json.loads((ROOT/"research/v33_signal_state.json").read_text())
    r=build_report(univ,short,state)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n")
    print("V33 BROAD SPORTS RESEARCH",json.dumps(
        {k:v for k,v in r.items() if k not in ("notes","research_queue")},
        sort_keys=True))
    print("NO TELEGRAM, NO LIVE WALLET EXPANSION, NO ORDERS")


if __name__=="__main__":
    main()
