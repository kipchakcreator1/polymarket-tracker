#!/usr/bin/env python3
"""Read-only, evidence-based audit of sports BUY alerts and sampled wallet picks.

NOT a trading strategy. A profitable closed position is NOT a winning sports
prediction. Never infer a final winner from closed PnL or stale Gamma prices.
Unknown, disputed, unverified-category, partially filled and unresolved data
are excluded from the certified directional denominator.
"""
import datetime
import json
import math
import os
from collections import defaultdict
from pathlib import Path
import time

import smart_tracker_v2 as api
from tools import v33_signal_bot as bot

ROOT = Path("research")
STATE_PATH = ROOT / "v33_signal_state.json"
SHORTLIST_PATH = ROOT / "v33_wallet_shortlist.json"
OUTPUT_PATH = ROOT / "v33_truth_report.json"
WALLETS_PER_RUN = max(1, min(5, int(os.getenv("V33_TRUTH_WALLETS_PER_RUN", "3"))))
TRADE_PAGES = 4
TRADE_PAGE_SIZE = 250
MAX_CONDITIONS_PER_WALLET = 90
MAX_SIGNAL_CONDITIONS = 90  # bounded refresh per run; retain previous observations
MAX_WALLET_OBSERVATIONS = 30


def seq(value):
    """Gamma encodes arrays as strings; refuse corrupt and ambiguous formats."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return value if isinstance(value, list) else None


def resolved_payout(row):
    """Return payout vector only for an explicitly final market resolution."""
    if not isinstance(row, dict) or str(row.get("status", "")).lower() != "resolved":
        return None
    if not row.get("resolved_at"):
        return None
    pays = row.get("payouts")
    if not isinstance(pays, list) or len(pays) < 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v < 0 for v in pays):
        return None
    if not math.isclose(sum(pays), 1_000_000, abs_tol=1):
        return None
    return pays


def resolved_at(row):
    try:
        raw = row["resolved_at"]
        dt = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return int(dt.timestamp()) if dt.tzinfo else None
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


def token_index(market, token):
    """Don't guess token index: Gamma token and position arrays are aligned."""
    token = str(token)
    if not token or token == "None":
        return None
    for key in ("clobTokenIds", "positionIds"):
        tokens = seq(market.get(key))
        if not tokens:
            continue
        matches = [i for i, value in enumerate(tokens) if str(value) == token]
        if len(matches) == 1:
            return matches[0]
    return None


def official_sports(market):
    if not isinstance(market, dict):
        return False
    return bot.official_sport_market(market) or any(
        bot.official_sport_market(e) for e in market.get("events", [])
        if isinstance(e, dict))


def verdict(market, resolution, token):
    """Return TRUE/FALSE only for verified, fully decisive sports resolutions."""
    if not official_sports(market):
        return "not_verified_sport"
    pays = resolved_payout(resolution)
    idx = token_index(market, token)
    if pays is None or idx is None or idx >= len(pays):
        return "unknown"
    # A refund/half payout is neither a normal win nor loss.
    if all(math.isclose(v, 0, abs_tol=1) or
           math.isclose(v, 1_000_000, abs_tol=1) for v in pays):
        return "win" if math.isclose(pays[idx], 1_000_000, abs_tol=1) else "loss"
    return "push_or_partial"


def market_data(condition, cache):
    if condition in cache:
        return cache[condition]
    rows = api.request_json("/markets", {"condition_ids": condition},
                            base=bot.GAMMA_API, tries=2, timeout=12)
    if not isinstance(rows, list):
        raise ValueError("Gamma metadata invalid")
    rows = [m for m in rows if isinstance(m, dict) and
            str(m.get("conditionId") or "").lower() == condition]
    value = rows[0] if len(rows) == 1 else None
    cache[condition] = value
    return value


def resolution_data(condition, cache):
    if condition in cache:
        return cache[condition]
    result = api.request_json("/v2/resolutions", {"condition": condition},
                              tries=2, timeout=12)
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise ValueError("Resolution API malformed")
    matches = [x for x in result["data"] if isinstance(x, dict) and
               str(x.get("condition_id") or "").lower() == condition]
    if len(matches) > 1:
        raise ValueError("Ambiguous market resolution")
    value = matches[0] if matches else None
    cache[condition] = value
    return value


def classify(condition, token, gamma_cache, resolution_cache):
    market = market_data(condition, gamma_cache)
    if not market:
        return "unknown", None
    if not official_sports(market):
        return "not_verified_sport", None
    resolution = resolution_data(condition, resolution_cache)
    if resolution is None:
        return "unknown", None
    result = verdict(market, resolution, token)
    return result, resolved_at(resolution)


def safe_condition(value):
    value = str(value or "").lower()
    return value if bot.v32.discovery.CONDITION_RE.fullmatch(value) else None


def audit_signals(sent, gamma_cache, resolution_cache, previous_rows=None):
    """Keep final verified evidence, refresh unresolved rows without faking PnL."""
    saved = { (x.get("wallet"), x.get("condition"), x.get("token_id")): x
              for x in (previous_rows or []) if isinstance(x, dict) }
    results = []
    work = 0
    for key, stamp in sorted(sent.items(), key=lambda x: -int(x[1])):
        parts = key.split("|")
        if len(parts) != 3 or not api.WALLET_RE.fullmatch(parts[0]):
            continue
        wallet, condition, token = parts
        if not safe_condition(condition) or not token:
            continue
        old = saved.get((wallet, condition, token))
        if old and old.get("result") in ("win", "loss", "push_or_partial",
                                          "not_verified_sport"):
            row = old
        elif work >= MAX_SIGNAL_CONDITIONS:
            row = old or {"wallet": wallet, "condition": condition,
                          "token_id": token, "alert_at": stamp,
                          "settled_at": None, "result": "not_checked"}
        else:
            work += 1
            try:
                label, settled = classify(condition, token, gamma_cache, resolution_cache)
            except Exception:
                label, settled = "api_error", None
            row = {"wallet": wallet, "condition": condition,
                   "token_id": token, "alert_at": stamp,
                   "settled_at": settled, "result": label}
        results.append(row)
    return results


def fetch_wallet_trades(wallet):
    """Bounded cursor walk, with explicit completeness flag for truncation."""
    if not api.WALLET_RE.fullmatch(wallet):
        raise ValueError("Bad wallet address")
    cursor = None
    seen_cursors = set()
    rows = []
    for _ in range(TRADE_PAGES):
        params = {"user": wallet, "limit": TRADE_PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        value = api.request_json("/v2/trades", params, tries=2, timeout=14)
        if not (isinstance(value, dict) and isinstance(value.get("data"), list)
                and isinstance(value.get("pagination"), dict)):
            raise ValueError("Unexpected trades response")
        for row in value["data"]:
            if not isinstance(row, dict) or str(row.get("proxy_wallet", "")).lower() != wallet:
                raise ValueError("Malformed trades / wallet mismatch")
            rows.append(row)
        page = value["pagination"]
        if page.get("has_more") is False:
            return rows, True
        nxt = page.get("next_cursor")
        if (page.get("has_more") is not True or
                not isinstance(nxt, str) or not nxt or nxt in seen_cursors):
            raise ValueError("Invalid trades cursor")
        seen_cursors.add(nxt)
        cursor = nxt
    return rows, False


def candidate_positions(rows):
    """One directional market pick, not one independent win per fill.

    Buying both sides is not a prediction; sell-before-resolution is noted as
    cashout and MUST NOT be confused with end-result correctness.
    """
    grouped = defaultdict(lambda: {"buys": defaultdict(float), "sold": False,
                                   "first": 0, "fill_count": 0})
    for row in rows:
        condition = safe_condition(row.get("condition_id"))
        token = str(row.get("token_id") or "")
        side = row.get("side")
        try:
            stamp = int(row.get("timestamp") or 0)
            size = float(row.get("size"))
            price = float(row.get("price"))
        except (ValueError, TypeError, OverflowError):
            continue
        if not condition or not token or side not in ("BUY", "SELL"):
            continue
        if stamp <= 0 or not (math.isfinite(size) and math.isfinite(price)
                             and size > 0 and 0 < price < 1):
            continue
        rec = grouped[condition]
        rec["first"] = min(rec["first"], stamp) if rec["first"] else stamp
        if side == "BUY":
            rec["buys"][token] += price * size
        else:
            rec["sold"] = True
        rec["fill_count"] += 1
    result = []
    for condition, rec in grouped.items():
        if len(rec["buys"]) != 1:
            continue
        token, dollars = next(iter(rec["buys"].items()))
        if dollars < 10:
            continue
        result.append((condition, token, rec["first"], rec["sold"]))
    return sorted(result, key=lambda x: -x[2])


def sample_wallet(wallet, gamma_cache, resolution_cache):
    trades, complete = fetch_wallet_trades(wallet)
    picks = candidate_positions(trades)[:MAX_CONDITIONS_PER_WALLET]
    verified = []
    statuses = defaultdict(int)
    for condition, token, first, cashout in picks:
        try:
            label, ended = classify(condition, token, gamma_cache, resolution_cache)
        except Exception:
            label, ended = "api_error", None
        statuses[label] += 1
        if label in ("win", "loss") and ended is not None:
            # A fill at/after settlement is never evidence of forecasting.
            if first > ended:
                continue
            verified.append({"condition": condition, "token_id": token,
                             "first_observed_buy_at": first, "resolved_at": ended,
                             "result": label, "sold_before_or_after": cashout})
    verified.sort(key=lambda x: -x["resolved_at"])
    last = verified[:MAX_WALLET_OBSERVATIONS]
    wins = sum(x["result"] == "win" for x in last)
    n = len(last)
    # When the trade feed did not finish, earlier buys/sells may be missing:
    # record observed directional sample, but NOT a certified true winrate.
    certified = complete and n >= 20
    return {"wallet": wallet, "trades_fetched": len(trades),
            "trade_history_complete": complete,
            "markets_considered": len(picks), "status_counts": dict(statuses),
            "resolved_sports_sample": n, "wins": wins, "losses": n-wins,
            "directional_win_fraction_observed": round(wins / n, 4) if n else None,
            "certified_sample": certified,
            "cashout_marked": sum(x["sold_before_or_after"] for x in last),
            "events": last}


def choose_wallets(shortlist, previous):
    eligible = [r["wallet"] for r in shortlist.get("shortlist", [])
                if isinstance(r, dict) and
                api.WALLET_RE.fullmatch(str(r.get("wallet") or "")) and
                int(r.get("last_sports_buy") or 0) > 0]
    if not eligible:
        return [], 0
    offset = int(previous.get("next_wallet_index") or 0) % len(eligible)
    selected = [eligible[(offset+i) % len(eligible)]
                for i in range(min(WALLETS_PER_RUN, len(eligible)))]
    return selected, (offset + len(selected)) % len(eligible)


def aggregate(rows):
    return {"win": sum(x["result"] == "win" for x in rows),
            "loss": sum(x["result"] == "loss" for x in rows),
            "unresolved_or_uncertain": sum(x["result"] == "unknown" for x in rows),
            "not_verified_sport": sum(x["result"] == "not_verified_sport" for x in rows),
            "push_or_partial": sum(x["result"] == "push_or_partial" for x in rows),
            "api_error": sum(x["result"] == "api_error" for x in rows),
            "not_checked": sum(x["result"] == "not_checked" for x in rows)}


def main():
    now = int(time.time())
    state = json.loads(STATE_PATH.read_text())
    shortlist = json.loads(SHORTLIST_PATH.read_text())
    previous = {}
    if OUTPUT_PATH.exists():
        try:
            previous = json.loads(OUTPUT_PATH.read_text())
        except (ValueError, OSError):
            pass
    gamma_cache, resolution_cache = {}, {}
    signal_rows = audit_signals(state.get("sent", {}), gamma_cache,
        resolution_cache, previous.get("alerts", {}).get("rows"))
    wallets, next_index = choose_wallets(shortlist, previous)
    wallet_results = dict(previous.get("wallets") or {})
    for wallet in wallets:
        try:
            wallet_results[wallet] = sample_wallet(wallet, gamma_cache, resolution_cache)
            print("TRUE SPORTS SAMPLE:", wallet[:12],
                  wallet_results[wallet]["resolved_sports_sample"],
                  "complete=", wallet_results[wallet]["trade_history_complete"],
                  "certified=", wallet_results[wallet]["certified_sample"])
        except Exception as exc:
            print("TRUE SPORTS ERROR:", wallet[:12], type(exc).__name__, str(exc)[:90])
            # Failed refreshes never erase earlier results or forge wins.
    report = {"schema": "v33_true_sports_audit_1", "updated_at": now,
              "method": "v2 trades + exact Gamma token mapping + final v2 payouts; "
                        "directional results, not realized PnL or copy ROI",
              "next_wallet_index": next_index,
              "alerts": {"summary": aggregate(signal_rows), "rows": signal_rows},
              "wallets": wallet_results,
              "disclaimer": "20 resolved observations are preliminary. A partial "
                "trade feed is explicitly not certified. Cashouts remain distinct; "
                "past BUY prices and actual copied fills were not recorded."}
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False,
                                     separators=(",", ":")) + "\n")
    print("V33 TRUE SPORTS AUDIT:", json.dumps({
        "signals": report["alerts"]["summary"], "wallets_checked": len(wallets),
        "wallets_observed": len(wallet_results),
        "certified": sum(bool(x.get("certified_sample")) for x in wallet_results.values())
    }, sort_keys=True))


if __name__ == "__main__":
    main()
