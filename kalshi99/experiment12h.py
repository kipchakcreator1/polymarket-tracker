#!/usr/bin/env python3
"""12-hour Kalshi 98-99¢ PROSPECTIVE PAPER study.

Public market GET requests only. Never imports or executes an authenticated
trading client. Two INDEPENDENT simulated $4 portfolios; first accepts existing 98-99c
quotes regardless of whether settlement is inside this 12-hour study. Observations and
maker intents are NOT fills. This is not a validated trading strategy.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from kalshi99 import scanner as base

START = datetime(2026, 10, 14, 6, 54, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 18, 54, tzinfo=timezone.utc)
CADENCE_SECONDS = 300
MARKET_PAGES = 12
MARKET_PAGE_SIZE = 500
BOOKS_PER_PASS = 60
HORIZON_HOURS = 72
DEFAULT_STATE = "research/kalshi-12h-state.json"
DEFAULT_SNAPSHOTS = "research/kalshi-12h-snapshots.jsonl"
DEFAULT_REPORT = "artifacts/kalshi-12h-latest.json"
DEFAULT_FINAL = "research/kalshi-12h-final.json"


def blank_state() -> dict:
    return {
        "schema": 1, "mode": "PAPER_ONLY_12H", "start_utc": START.isoformat(),
        "end_utc": END.isoformat(), "last_bucket": -1,
        "scans": 0, "api_errors": 0, "total_listed_markets": 0,
        "total_indicative_candidates": 0, "total_verified_quotes": 0,
        "unique_verified_market_sides": [], "maker_proposals": 0,
        "finalized_at": None,
        "portfolios": {"taker_12h": base.new_state(), "taker_near_1h": base.new_state()},
    }


def load_state(path: str) -> dict:
    p = Path(path)
    state = json.loads(p.read_text()) if p.exists() else blank_state()
    if (state.get("schema") != 1 or state.get("mode") != "PAPER_ONLY_12H"
            or state.get("start_utc") != START.isoformat()
            or state.get("end_utc") != END.isoformat()):
        raise ValueError("Unexpected experiment state or fixed 12-hour window")
    return state


def portfolio_stats(portfolio: dict) -> dict:
    hist = portfolio["history"]
    cash = D(str(portfolio["cash_usd"]))
    committed = sum((D(str(p["cost_usd"])) for p in portfolio["positions"]), D(0))
    realized = sum((D(str(p["realized_pnl_usd"])) for p in hist), D(0))
    return {
        "starting_usd": "4.00",
        "cash_usd": base.money(cash),
        "committed_usd_cost_basis": base.money(committed),
        "opened_total": len(hist) + len(portfolio["positions"]),
        "open": len(portfolio["positions"]), "settled": len(hist),
        "wins": sum(1 for p in hist if p.get("won")),
        "losses": sum(1 for p in hist if not p.get("won")),
        "realized_pnl_usd": base.money(realized),
        "pending_contracts": [p["ticker"] for p in portfolio["positions"]],
        "note": "Open contracts are not valued at settlement; no real exchange executions.",
    }


def make_report(state: dict, now: datetime, status: str, scan: dict | None = None) -> dict:
    portfolios = {n: portfolio_stats(p) for n, p in state["portfolios"].items()}
    return {
        "experiment": "Kalshi 98-99c 12-hour read-only study",
        "mode": "NO_REAL_ORDERS", "status": status, "as_of_utc": now.isoformat(),
        "starts_utc": START.isoformat(), "ends_utc": END.isoformat(),
        "cadence_target_seconds": CADENCE_SECONDS,
        "scans_completed": state["scans"],
        "market_rows_examined_cumulative": state["total_listed_markets"],
        "indicative_candidates_cumulative": state["total_indicative_candidates"],
        "verified_quotes_cumulative": state["total_verified_quotes"],
        "unique_verified_market_sides": len(state["unique_verified_market_sides"]),
        "maker_price_proposals_seen": state["maker_proposals"],
        "maker_filled_simulated": 0,  # Explicit: no queue/print evidence -> NO fills.
        "maker_actual_pnl_known": False,
        "api_errors": state["api_errors"],
        "portfolios": portfolios,
        "latest_scan": scan,
        "limitations": [
            "Sampled executable ask/depth is not proof an actual taker order would fill after latency.",
            "Hypothetical maker limits never count as fills; maker comparison only tracks indicative price advantage.",
            "No independently estimated true winning probabilities.",
            "Two simulated $4 portfolios are mutually independent comparisons; each has only ONE open position.",
            "The taker_12h portfolio key is kept for history compatibility, but NO longer filters trades to 12h; it admits ANY sampled 98-99c quote within the 72h market-discovery horizon.",
            "Any still-open contracts at 12-hour cutoff are excluded from realized P/L.",
            "Only a subset of Kalshi markets/orderbooks is visited each run; GitHub cron may be delayed.",
            "Fees are a conservative 1-cent rounded generic taker estimate, not a verified series fee.",
        ],
    }


def scan(now: datetime, state: dict) -> dict:
    settlements = {}
    for strategy, portfolio in state["portfolios"].items():
        settled, errors = base.settle_positions(portfolio)
        state["api_errors"] += errors
        settlements[strategy] = settled

    markets, pages = base.list_open_markets(
        MARKET_PAGES, MARKET_PAGE_SIZE, HORIZON_HOURS, now)
    candidates = []
    for market in markets:
        candidates.extend(base.candidate_sides(market, now, HORIZON_HOURS))
    candidates.sort(key=lambda q: (q["hours_to_close"], q["indicative_ask"], q["ticker"]))
    bucket = int((now - START).total_seconds() // CADENCE_SECONDS)

    # Near-settlement candidates first, rotate extra orderbook reads across
    # all other candidates. Deduplicate any overlap before hitting public API.
    selected = candidates[:35]
    if candidates:
        shift = (bucket * 25) % len(candidates)
        rotation = candidates[shift:] + candidates[:shift]
        seen = {(q["ticker"], q["side"]) for q in selected}
        for q in rotation:
            key = (q["ticker"], q["side"])
            if key not in seen:
                selected.append(q)
                seen.add(key)
            if len(selected) >= BOOKS_PER_PASS:
                break
    quotes, book_errors = base.quote_candidates(
        selected, D("4.00"), BOOKS_PER_PASS)
    state["api_errors"] += book_errors
    state["total_listed_markets"] += len(markets)
    state["total_indicative_candidates"] += len(candidates)
    state["total_verified_quotes"] += len(quotes)
    unique = set(state["unique_verified_market_sides"])
    for quote in quotes:
        unique.add(quote["ticker"] + ":" + quote["side"])
    state["unique_verified_market_sides"] = sorted(unique)
    state["maker_proposals"] += len(quotes)

    new_entries = {}
    for strategy, maximum_hours in (("taker_12h", None), ("taker_near_1h", 1)):
        portfolio = state["portfolios"][strategy]
        # 'taker_12h' is a legacy state key, NOT a hold-to-12h rule.
        # Accept EXISTING 98-99c quotes NOW, even if their official
        # close_time is days later. Keep the short-horizon portfolio separate.
        # Use only sampled ask/depth, NOT mid or last trade.
        feasible = sorted(
            (q for q in quotes if maximum_hours is None
             or q["hours_to_close"] <= maximum_hours),
            key=lambda q: (q["hours_to_close"], D(q["quote"]), q["ticker"]))
        new_entries[strategy] = None
        for quote in feasible:
            if base.paper_enter(portfolio, quote, now):
                new_entries[strategy] = {
                    "ticker": quote["ticker"], "side": quote["side"],
                    "cost_usd": quote["paper_cost"], "price": quote["quote"],
                    "qty": quote["paper_contracts"],
                    "classification": "optimistic_taker_at_sampled_ask_not_real_fill",
                }
                break

    quote_snapshots = [{
        "ticker": q["ticker"], "side": q["side"], "title": q["title"],
        "hours_to_close": q["hours_to_close"],
        "sampled_ask": q["quote"], "available_at_best_ask": q["depth_contracts"],
        "hypothetical_qty": q["paper_contracts"],
        "estimated_taker_fee_usd": q["estimated_fee"],
        "win_profit_taker_usd": q["profit_if_yes_correct"],
        "loss_taker_usd": q["loss_if_wrong"],
        "break_even_probability_taker": q["break_even_probability"],
        "maker_limit_1c_below_ask": str(max(D("0.01"), D(q["quote"]) - D("0.01"))),
        "maker_fill_observed": False,  # no trade-through/queue data
        "independent_true_probability": None,
        "close_time": q["close_time"],
    } for q in quotes]
    state["scans"] += 1
    return {
        "sampled_at_utc": now.isoformat(), "bucket": bucket,
        "market_pages": pages, "active_market_rows": len(markets),
        "indicative_candidates": len(candidates), "orderbooks_requested": len(selected),
        "orderbook_errors": book_errors, "verified_quotes": len(quote_snapshots),
        "settled_this_scan": settlements,
        "new_paper_positions": new_entries,
        "quotes": quote_snapshots,
    }


def run(now: datetime | None = None, state_path: str = DEFAULT_STATE,
        snapshots_path: str = DEFAULT_SNAPSHOTS, report_path: str = DEFAULT_REPORT,
        final_path: str = DEFAULT_FINAL) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = load_state(state_path)
    if now < START:
        report = make_report(state, now, "WAITING_FOR_START")
        base.save_json(report_path, report)
        return report
    if now >= END:
        report = make_report(state, now, "FINISHED_12H")
        if state["finalized_at"] is None:
            state["finalized_at"] = now.isoformat()
            base.save_json(state_path, state)
            base.save_json(final_path, report)
        base.save_json(report_path, report)
        return report
    bucket = int((now - START).total_seconds() // CADENCE_SECONDS)
    if bucket <= state["last_bucket"]:
        report = make_report(state, now, "ALREADY_SCANNED_BUCKET")
        base.save_json(report_path, report)
        return report
    try:
        current = scan(now, state)
        state["last_bucket"] = bucket
        report = make_report(state, now, "ACTIVE", current)
        p = Path(snapshots_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(current, ensure_ascii=False, sort_keys=True) + "\n")
        base.save_json(state_path, state)
        base.save_json(report_path, report)
        return report
    except Exception as exc:
        # Never turn missing data/API errors into fabricated fills or winnings.
        report = make_report(state, now, "SCAN_FAILED")
        report["error_type"] = type(exc).__name__
        base.save_json(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description="Kalshi 12h prospective paper-only experiment")
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--snapshots", default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--final", default=DEFAULT_FINAL)
    args = parser.parse_args()
    report = run(
        state_path=args.state, snapshots_path=args.snapshots,
        report_path=args.report, final_path=args.final)
    print(json.dumps({
        "status": report["status"],
        "scans_completed": report["scans_completed"],
        "verified_quotes_cumulative": report["verified_quotes_cumulative"],
        "unique_verified_market_sides": report["unique_verified_market_sides"],
        "portfolios": report["portfolios"],
        "ends_utc": report["ends_utc"],
    }, indent=2))


if __name__ == "__main__":
    main()
