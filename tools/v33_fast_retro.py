#!/usr/bin/env python3
"""Immediate retrospective risk diagnostic, NEVER Telegram qualification.

Read-only, using public historical official resolved payouts already audited.
Calculates *observed* hold to settlement at the original wallet's FIRST BUY
price (not our fill), plus unknown-outcome stress and cashout flags. Research
only; cannot establish a tradable edge without forward book-priced validation.
"""
import json
import math
from collections import defaultdict
from pathlib import Path

from tools.v33_evidence_rank import wilson_lower, unique_sports_events

SOURCE = Path("research/v33_truth_report.json")
OUTPUT = Path("research/v33_fast_retro.json")


def analyze_wallet(wallet, data):
    pos = data.get("position_truth") or {}
    events, problems = unique_sports_events(pos.get("events") or [])
    by_league = defaultdict(list)
    for e in events:
        by_league[e["league"].upper()].append(e)
    result = {}
    for league, rows in sorted(by_league.items()):
        n = len(rows)
        wins = sum(x["direction"] == "win" for x in rows)
        priced = []
        for e in rows:
            try:
                price = float(e.get("source_first_buy_price"))
                if math.isfinite(price) and 0 < price < 1:
                    priced.append((e, price))
            except (TypeError, ValueError):
                pass
        price_complete = len(priced) == n
        # All equally-staked hypothetical buys, held to settlement; no fills
        # are assumed for followers; original wallets may have cashed out.
        simulated = sum(
            (1 / price - 1) if e["direction"] == "win" else -1
            for e, price in priced)
        result[league] = {
            "verified_unique_events_in_observed_sample": n,
            "wins": wins, "losses": n - wins,
            "observed_direction_accuracy": round(wins/n, 4),
            "wilson_lower_95pct_on_observed_only": round(
                wilson_lower(wins, n), 4),
            "events_with_source_buy_price": len(priced),
            "source_buy_price_coverage_complete": price_complete,
            "source_first_buy_hold_roi_no_fees": round(
                simulated/n, 4) if price_complete else None,
            "source_mean_first_buy_price": round(
                sum(price for _, price in priced)/n, 4)
                if price_complete else None,
            "paper_copy_roi": None,
        }
    raw_status = data.get("status_counts") or {}
    uncertain = data.get("unverified_final_or_mapping_count")
    if uncertain is None:
        uncertain = sum(value for key, value in raw_status.items()
                        if key not in ("win", "loss", "push_or_partial",
                                       "unknown_resolution_pending",
                                       "not_verified_sport"))
    unknowns = max(0, int(uncertain))
    verified = sum(v["verified_unique_events_in_observed_sample"]
                   for v in result.values())
    wins = sum(v["wins"] for v in result.values())
    total_for_stress = verified + unknowns
    return {
        "wallet": wallet,
        "verified_unique_events": verified,
        "by_league": result,
        "unverified_final_or_mapping_markets": unknowns,
        "stress_scenario_if_all_ambiguous_are_losses": round(
            wins/total_for_stress, 4) if total_for_stress else None,
        "stress_scenario_if_all_ambiguous_are_wins": round(
            (wins+unknowns)/total_for_stress, 4) if total_for_stress else None,
        "stress_not_a_statistical_confidence_interval": True,
        "missing_or_duplicate_event_ids": problems,
        "maker_taker_window_complete": bool(data.get("trade_window_complete")),
        "position_history_complete": bool(pos.get("position_history_complete")),
        "historical_winrate_certified": bool(
            data.get("certified_sample") and pos.get("certified_sample")
            and data.get("trade_window_complete")
            and data.get("market_scan_complete")
            and pos.get("position_history_complete")
            and pos.get("position_market_scan_complete")
            and not unknowns and verified >= 30
            and not any(problems.values())),
        "large_unredeemed_losses": (pos.get("position_accounting") or {}).get(
            "unredeemed_major_losers_100usd"),
        "notes": "The last 30 selected verified outcomes are not necessarily "
                 "the last 30 completed events if final states were missing. "
                 "Source BUY price is not our executable ask. All ROI figures "
                 "exclude trading fees, slippage and actual cashout history.",
    }


def report(source):
    wallets = [analyze_wallet(w,v) for w,v in
               (source.get("wallets") or {}).items() if isinstance(v, dict)]
    wallets.sort(key=lambda x: (-x["verified_unique_events"],
                                x["unverified_final_or_mapping_markets"],
                                x["wallet"]))
    enough = [x for x in wallets if x["verified_unique_events"] >= 20]
    return {
        "schema": "v33_fast_retro_1",
        "source_updated_at": source.get("updated_at"),
        "total_wallets_in_audit": len(wallets),
        "wallets_with_at_least_20_verified_unique_events": len(enough),
        "historically_certified_wallets": sum(
            x["historical_winrate_certified"] for x in wallets),
        "observed_not_verified": True,
        "wallets": wallets,
        "disclaimer": "Research only. NOT signal approval. 20-30 observed "
                      "historical outcomes are an incomplete subset; unresolved "
                      "markets and selective historical exits can bias ROI. "
                      "Source trade first-buy prices are not follower fill "
                      "prices or verified copy profit."
    }


def main():
    snapshot = json.loads(SOURCE.read_text())
    result = report(snapshot)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False,
                                 separators=(",", ":"))+"\n")
    for row in result["wallets"][:6]:
        print("RETRO WALLET", row["wallet"][:14],
              "unique", row["verified_unique_events"],
              "uncertain", row["unverified_final_or_mapping_markets"],
              "source_priced_leagues", {
                  league: v["source_first_buy_hold_roi_no_fees"]
                  for league,v in row["by_league"].items()},
              "certified", row["historical_winrate_certified"])
    print("V33 FAST RETRO SUMMARY", json.dumps({
        "wallets": result["total_wallets_in_audit"],
        "min20_observed": result["wallets_with_at_least_20_verified_unique_events"],
        "certified": result["historically_certified_wallets"]}))


if __name__ == "__main__":
    main()
