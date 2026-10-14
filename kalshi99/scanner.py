#!/usr/bin/env python3
"""Kalshi 98-99 cent research scanner. PUBLIC GET ONLY. No account or orders.

An intentionally simple price-only baseline, NOT an independent win prediction.
Paper bankroll defaults to $4; exactly one open position and no paper selling.
All fills are hypothetical at currently visible top-of-book size. Standard
Kalshi taker fees are conservatively rounded UP to whole cents and may differ
from actual series-specific fees. Do not use this for live execution.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
D = Decimal
CENT = D("0.01")
ONE = D("1")
START_BALANCE = D("4.00")
DEFAULT_STATE = "research/kalshi-99c-state.json"
DEFAULT_REPORT = "artifacts/kalshi-99c-latest.json"


def money(x: Decimal) -> str:
    return format(x.quantize(CENT), ".2f")


def parse_price(value) -> Decimal | None:
    try:
        p = D(str(value))
        if not p.is_finite() or not (D("0") < p < ONE):
            return None
        return p
    except (ValueError, TypeError, ArithmeticError):
        return None


def parse_qty(value) -> int:
    try:
        v = D(str(value))
        if not v.is_finite() or v <= 0:
            return 0
        return int(v.to_integral_value(rounding=ROUND_FLOOR))
    except (ValueError, TypeError, ArithmeticError):
        return 0


def fee_taker(price: Decimal, qty: int) -> Decimal:
    """Conservative estimate of standard taker fee, rounded to a whole cent."""
    raw = D("0.07") * D(qty) * price * (ONE - price)
    return (raw / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


def max_affordable(balance: Decimal, price: Decimal, available: int, max_qty: int = 4) -> int:
    for qty in range(min(available, max_qty), 0, -1):
        if price * qty + fee_taker(price, qty) <= balance:
            return qty
    return 0


def get_json(endpoint: str, params: dict | None = None, retries: int = 2) -> dict:
    """Fixed public host + only GET; no credentials accepted or transmitted."""
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(
        BASE_URL + endpoint + query,
        headers={"User-Agent": "Kalshi99cPaperResearch/0.1", "Accept": "application/json"},
        method="GET",
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=16) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
            time.sleep(1 + attempt * 2)
        except (TimeoutError, urllib.error.URLError):
            if attempt == retries:
                raise
            time.sleep(1 + attempt * 2)
    raise RuntimeError("Public API unavailable")


def parse_utc(value: str | None) -> datetime | None:
    try:
        if not value:
            return None
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def list_open_markets(pages: int, page_size: int = 500) -> tuple[list[dict], int]:
    found = []
    cursor = ""
    fetched = 0
    for _ in range(pages):
        params = {"status": "open", "limit": page_size}
        if cursor:
            params["cursor"] = cursor
        data = get_json("/markets", params)
        batch = data.get("markets", [])
        if not isinstance(batch, list):
            raise ValueError("Unexpected public market response")
        fetched += 1
        found.extend(batch)
        new_cursor = data.get("cursor") or ""
        if not batch or not new_cursor or new_cursor == cursor:
            break
        cursor = new_cursor
    return found, fetched


def candidate_sides(market: dict, now: datetime, max_horizon_hours: int = 72) -> list[dict]:
    if market.get("status") != "open" or market.get("market_type", "binary") != "binary":
        return []
    if market.get("mve_collection_ticker"):
        return []  # exclude potentially nonstandard combo payouts
    expiry = parse_utc(market.get("close_time"))
    if not expiry:
        return []
    remaining_hours = (expiry - now).total_seconds() / 3600
    if remaining_hours < (10 / 60) or remaining_hours > max_horizon_hours:
        return []
    results = []
    for side in ("yes", "no"):
        price = parse_price(market.get(side + "_ask_dollars"))
        if price is None or price < D("0.98") or price > D("0.99"):
            continue
        if not market.get("ticker"):
            continue
        results.append({
            "ticker": market["ticker"],
            "title": market.get("title") or market["ticker"],
            "side": side,
            "indicative_ask": str(price),
            "close_time": expiry.isoformat(),
            "hours_to_close": round(remaining_hours, 2),
        })
    return results


def top_executable_quote(orderbook: dict, side: str) -> tuple[Decimal, int] | None:
    """Kalshi publishes bids only. Opposite bid == buy ask at (1 - bid)."""
    opposite = "no_dollars" if side == "yes" else "yes_dollars"
    levels = (orderbook.get("orderbook_fp") or {}).get(opposite) or []
    usable = []
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        bid, qty = parse_price(level[0]), parse_qty(level[1])
        if bid is not None and qty >= 1:
            usable.append((ONE - bid, qty))
    if not usable:
        return None
    return min(usable, key=lambda x: x[0])


def quote_candidates(candidates: list[dict], balance: Decimal, book_limit: int) -> tuple[list[dict], int]:
    verified, errors = [], 0
    for candidate in candidates[:book_limit]:
        try:
            response = get_json("/markets/" + urllib.parse.quote(candidate["ticker"], safe="") + "/orderbook", {"depth": 5})
            quote = top_executable_quote(response, candidate["side"])
        except (urllib.error.URLError, ValueError, TimeoutError, KeyError, OSError):
            errors += 1
            continue
        if not quote:
            continue
        price, depth = quote
        # No implied fills beyond best price level, no fractional contracts.
        if not (D("0.98") <= price <= D("0.99")):
            continue
        qty = max_affordable(balance, price, depth)
        if qty < 1:
            continue
        fee = fee_taker(price, qty)
        cost = price * qty + fee
        verified.append({
            **candidate, "quote": str(price), "depth_contracts": depth,
            "paper_contracts": qty, "estimated_fee": money(fee),
            "paper_cost": money(cost),
            "profit_if_yes_correct": money(D(qty) - cost),
            "loss_if_wrong": money(cost),
            "break_even_probability": round(float(cost / qty), 6),
            "estimated_fee_only": True,
            "independent_probability": None,
            "risk_note": "Price-only baseline; no independent evidence of 99.9% probability.",
        })
    # Prefer 98-cent quotes for larger gross payoff; not a forecast.
    verified.sort(key=lambda c: (D(c["quote"]), c["hours_to_close"], -c["depth_contracts"]))
    return verified, errors


def new_state() -> dict:
    return {
        "schema": 1, "mode": "PAPER_ONLY",
        "starting_usd": money(START_BALANCE),
        "cash_usd": money(START_BALANCE),
        "positions": [], "history": [], "seen": [],
    }


def load_state(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return new_state()
    state = json.loads(p.read_text(encoding="utf-8"))
    if state.get("schema") != 1 or state.get("mode") != "PAPER_ONLY":
        raise ValueError("State schema or mode mismatch")
    return state


def save_json(path: str, data: dict):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_name(p.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, p)


def settle_positions(state: dict) -> tuple[int, int]:
    settled = 0
    errors = 0
    active = []
    for pos in state["positions"]:
        try:
            market = get_json("/markets/" + urllib.parse.quote(pos["ticker"], safe="")).get("market") or {}
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            active.append(pos)
            errors += 1
            continue
        if market.get("status") != "settled" or market.get("result") not in ("yes", "no"):
            active.append(pos)  # no premature win/void guessing
            continue
        win = market["result"] == pos["side"]
        payout = D(pos["qty"]) if win else D("0")
        state["cash_usd"] = money(D(state["cash_usd"]) + payout)
        state["history"].append({
            **pos, "result": market["result"], "won": win,
            "payout_usd": money(payout),
            "realized_pnl_usd": money(payout - D(pos["cost_usd"])),
            "settled_at": datetime.now(timezone.utc).isoformat()
        })
        settled += 1
    state["positions"] = active
    return settled, errors


def paper_enter(state: dict, quote: dict, now: datetime) -> bool:
    if state["positions"]:
        return False
    key = quote["ticker"] + ":" + quote["side"]
    if key in state["seen"]:
        return False
    cost = D(quote["paper_cost"])
    cash = D(state["cash_usd"])
    if cost > cash or cost <= 0:
        return False
    state["cash_usd"] = money(cash - cost)
    state["positions"].append({
        "ticker": quote["ticker"], "title": quote["title"], "side": quote["side"],
        "qty": quote["paper_contracts"], "price": quote["quote"],
        "cost_usd": quote["paper_cost"],
        "fee_estimated_usd": quote["estimated_fee"],
        "entry_time": now.isoformat(), "close_time": quote["close_time"],
        "independent_probability": None, "baseline": "PRICE_ONLY"
    })
    state["seen"].append(key)
    state["seen"] = state["seen"][-10000:]
    return True


def run(args) -> dict:
    now = datetime.now(timezone.utc)
    state = load_state(args.state)
    settled, settlement_errors = settle_positions(state)
    markets, pages = list_open_markets(args.pages, args.page_size)
    shortlist = []
    for market in markets:
        shortlist.extend(candidate_sides(market, now, args.horizon))
    shortlist.sort(key=lambda c: (D(c["indicative_ask"]), c["hours_to_close"]))
    quotes, book_errors = quote_candidates(shortlist, D(state["cash_usd"]), args.books)
    entered = False
    # This is a naive PRICE-ONLY experimental baseline. NEVER a claim of alpha.
    if args.paper_trade and not state["positions"] and quotes:
        for quote in quotes:
            if paper_enter(state, quote, now):
                entered = True
                break
    report = {
        "timestamp": now.isoformat(), "mode": "READ_ONLY_PUBLIC_API",
        "strategy": "98-99c PRICE-ONLY BASELINE; probability not independently estimated",
        "real_trading_enabled": False,
        "pages_read": pages, "markets_scanned": len(markets),
        "indicative_candidates": len(shortlist), "orderbooks_checked": min(len(shortlist), args.books),
        "orderbook_errors": book_errors, "settlement_errors": settlement_errors,
        "paper_trading_enabled": args.paper_trade,
        "paper_entry_this_pass": entered, "settled_this_pass": settled,
        "cash_usd": state["cash_usd"],
        "committed_to_paper_positions_usd": money(sum((D(x["cost_usd"]) for x in state["positions"]), D("0"))),
        "paper_positions": state["positions"],
        "paper_history": state["history"][-50:],
        "verified_quotes": quotes[:20],
        "warning": "No independent outcome probability estimate. Fees conservative and series-specific fees may differ. Historical/scanned subset is not full Kalshi universe.",
    }
    save_json(args.state, state)
    save_json(args.report, report)
    return report


def main():
    parser = argparse.ArgumentParser(description="PUBLIC Kalshi 99c read-only paper scanner")
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--pages", type=int, default=6)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--books", type=int, default=25)
    parser.add_argument("--horizon", type=int, default=72)
    parser.add_argument("--paper-trade", action="store_true", help="Simulate up to $4 total, never send orders")
    args = parser.parse_args()
    if not (1 <= args.pages <= 25 and 1 <= args.page_size <= 1000 and 0 <= args.books <= 100 and 1 <= args.horizon <= 720):
        parser.error("Out-of-range scanner settings")
    report = run(args)
    print(json.dumps({
        k: report[k] for k in (
            "markets_scanned", "indicative_candidates", "orderbooks_checked",
            "orderbook_errors", "paper_entry_this_pass", "settled_this_pass",
            "cash_usd", "committed_to_paper_positions_usd"
        )
    }, indent=2))


if __name__ == "__main__":
    main()
