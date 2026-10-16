#!/usr/bin/env python3
"""v3.3 independent public-wallet research. NO orders or Telegram.

Incrementally grows a candidate universe from rotating active Polymarket
markets. A wallet is only a candidate until its history and actual future
copyability are evaluated. Never modifies the v3.1 or v3.2 tracker states.
"""
import collections
import concurrent.futures
import json
import math
import os
from pathlib import Path
import time

import smart_tracker_v2 as api
import wallet_discovery_v32 as discovery

STATE = Path(os.getenv("V33_RESEARCH_STATE", "research/v33_wallet_universe.json"))
SHORTLIST = Path(os.getenv("V33_SHORTLIST", "research/v33_wallet_shortlist.json"))
SOURCE = Path("state/smart_tracker_v3.json")
TARGET = max(1000, min(50000, int(os.getenv("V33_TARGET_WALLETS", "10000"))))
MAX_MARKETS = max(10, min(250, int(os.getenv("V33_MARKETS_PER_RUN", "180"))))
MAX_GAMMA_PAGES = max(2, min(24, int(os.getenv("V33_GAMMA_PAGES", "12"))))
SCORE_BATCH = max(0, min(200, int(os.getenv("V33_SCORE_BATCH", "100"))))
PAGE_SIZE = 50
SCORE_REFRESH = 7 * 86400
RECENT = 7 * 86400
CRYPTO_LOOKBACK = 3600
CRYPTO_WALLET_MIN_USD = 10.0
CRYPTO_ACTIVE_WINDOW = 24 * 3600
# Most recent four BTC/ETH/SOL/XRP 5m windows and two 15m windows.
CRYPTO_WINDOWS = (("5m", 300, 4), ("15m", 900, 2))
CRYPTO_ASSETS = ("btc", "eth", "sol", "xrp")
MID_LOW = 0.15
MID_HIGH = 0.85
EXPENSIVE = 0.95


def valid_wallet(wallet):
    return bool(api.WALLET_RE.fullmatch(str(wallet or "").lower()))


def load_universe(now):
    if STATE.exists():
        obj = json.loads(STATE.read_text(encoding="utf-8"))
        if obj.get("schema") != "v33_wallet_universe_1":
            raise ValueError("Unexpected v3.3 research schema")
        if not isinstance(obj.get("wallets"), dict):
            raise ValueError("Invalid v3.3 research wallet map")
        return obj
    # Seed once from a COPY of the v3.2 testing state. No writes to its state.
    original = json.loads(SOURCE.read_text(encoding="utf-8"))
    wallets = {}
    for address, meta in original.get("wallets", {}).items():
        address = address.lower()
        if not valid_wallet(address) or not isinstance(meta, dict):
            continue
        item = {"source": "v32_snapshot", "first_seen": now,
                "name": str(meta.get("name") or "")[:60]}
        for field in ("n", "wins", "winrate", "recent_realized_pnl",
                      "profit_factor", "gross_gains", "gross_losses"):
            if field in meta:
                item[field] = meta[field]
        if meta.get("checked_at"):
            item["score_checked_at"] = int(meta["checked_at"])
        wallets[address] = item
    return {"schema": "v33_wallet_universe_1", "created_at": now,
            "wallets": wallets, "runs": 0}


def choose_markets(markets, now, limit):
    """A stable high-volume core plus rotating middle/tail, not top-only."""
    ranked = sorted(markets.values(), key=lambda m: (-m["vol"], m["condition"]))
    if len(ranked) <= limit:
        return ranked
    core_n = min(50, limit // 3)
    selected = ranked[:core_n]
    rest = ranked[core_n:]
    half = len(rest) // 2
    bins = (rest[:half], rest[half:])
    slots = (limit - core_n) // 2
    for i, group in enumerate(bins):
        wanted = slots if i == 0 else limit - len(selected)
        if not group:
            continue
        start = ((now // 3600) * (31 + 13 * i)) % len(group)
        selected.extend(group[(start + j) % len(group)]
                        for j in range(min(wanted, len(group))))
    if len(selected) < limit:
        taken = {m["condition"] for m in selected}
        selected.extend(m for m in ranked
                        if m["condition"] not in taken)  # capped below
    return selected[:limit]


def fetch_markets(now):
    markets = {}
    errors = 0
    for page in range(MAX_GAMMA_PAGES):
        try:
            raw = api.request_json(
                "/markets", {"active": "true", "closed": "false",
                             "limit": PAGE_SIZE, "offset": page * PAGE_SIZE},
                base=discovery.GAMMA, tries=2, timeout=12)
            if not isinstance(raw, list):
                raise ValueError("Gamma response is not a list")
        except Exception as exc:
            errors += 1
            print("V33 GAMMA ERROR:", page, type(exc).__name__, str(exc)[:120])
            continue
        for m in raw:
            if not isinstance(m, dict):
                continue
            condition = str(m.get("conditionId") or "").lower()
            if not discovery.CONDITION_RE.fullmatch(condition):
                continue
            if m.get("closed") is True or m.get("active") is False:
                continue
            markets[condition] = {
                "condition": condition,
                "vol": api.asfloat(m.get("volume24hr"), 0)}
        if len(raw) < PAGE_SIZE:
            break
    return choose_markets(markets, now, MAX_MARKETS), len(markets), errors


def discover_short_crypto_markets(now):
    """Find *actual* recent 5m/15m markets by deterministic Gamma slug.

    The generic /markets page ranks broad markets and may not include these
    recurring crypto rounds. A missing/failed event is not fabricated.
    """
    candidates = []
    for asset in CRYPTO_ASSETS:
        for duration, seconds, lookback in CRYPTO_WINDOWS:
            start = (now // seconds) * seconds
            for i in range(lookback):
                tick = start - i * seconds
                candidates.append(asset + "-updown-" + duration + "-" + str(tick))
    def lookup(slug):
        raw = api.request_json("/events", {"slug": slug},
                               base=discovery.GAMMA, tries=2, timeout=10)
        if not isinstance(raw, list):
            raise ValueError("Gamma crypto events not list")
        selected = [x for x in raw if isinstance(x, dict) and
                    x.get("slug") == slug]
        if len(selected) != 1:
            return None
        matches = selected[0].get("markets") or []
        if not isinstance(matches, list) or len(matches) != 1:
            return None
        condition = str(matches[0].get("conditionId") or "").lower()
        if not discovery.CONDITION_RE.fullmatch(condition):
            return None
        return {"condition": condition, "vol": 0.0,
                "crypto_short": True, "slug": slug}
    found = {}
    errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(lookup, slug): slug for slug in candidates}
        for future in concurrent.futures.as_completed(futures):
            try:
                market = future.result()
                if market is not None:
                    found[market["condition"]] = market
            except Exception as exc:
                errors += 1
                print("V33 CRYPTO DISCOVERY ERROR:", type(exc).__name__,
                      str(exc)[:80])
    return list(found.values()), errors


def fetch_market_trades(market, now):
    condition = market["condition"]
    response = api.request_json(
        "/v2/trades", {"condition": condition, "limit": 200,
                       "taker_only": "false"}, tries=2, timeout=14)
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise ValueError("Unexpected v2/trades response")
    stats = {}
    for row in response["data"]:
        if not isinstance(row, dict):
            continue
        if str(row.get("condition_id", "")).lower() != condition:
            continue
        timestamp = int(api.asfloat(row.get("timestamp"), 0))
        if not (now - RECENT <= timestamp <= now + 60):
            continue
        wallet = str(row.get("proxy_wallet") or "").lower()
        if not valid_wallet(wallet):
            continue
        data = stats.setdefault(wallet, {"rows": 0, "mid_buys": 0,
                                         "expensive_buys": 0, "buy_rows": 0,
                                         "crypto_last_buy": 0})
        data["rows"] += 1
        if str(row.get("side") or "").upper() == "BUY":
            price = api.asfloat(row.get("price"), -1)
            if 0 < price < 1:
                data["buy_rows"] += 1
                data["mid_buys"] += int(MID_LOW <= price <= MID_HIGH)
                data["expensive_buys"] += int(price >= EXPENSIVE)
                # Observed source size is shares, so use size*price in USD.
                # Timestamp comes from the ACTUAL TRADE; re-reading one
                # market in a later research run cannot refresh recency.
                if (market.get("crypto_short") and
                        now - CRYPTO_LOOKBACK <= timestamp <= now+60 and
                        MID_LOW <= price <= MID_HIGH and
                        api.asfloat(row.get("size")) * price >= CRYPTO_WALLET_MIN_USD):
                    data["crypto_last_buy"] = max(data["crypto_last_buy"],
                                                  timestamp)
    return len(response["data"]), stats


def incorporate_sample(state, observations, now):
    wallets = state["wallets"]
    # Diversity (distinct sampled markets) first. Do not assume trading
    # volume or closed-position PnL implies prediction skill.
    ranked = sorted(observations.items(),
                    key=lambda kv: (-int(kv[1].get("crypto_last_buy") or 0),
                                    -kv[1]["markets"], -kv[1]["mid_buys"],
                                    -kv[1]["rows"], kv[0]))
    added = 0
    for wallet, sample in ranked:
        if wallet not in wallets:
            if len(wallets) >= TARGET:
                continue
            wallets[wallet] = {"source": "v33_public_market_trades",
                               "first_seen": now}
            added += 1
        item = wallets[wallet]
        item["last_market_seen"] = now
        if int(sample.get("crypto_last_buy") or 0) > int(item.get("crypto_last_buy") or 0):
            item["crypto_last_buy"] = int(sample["crypto_last_buy"])
        # These are SAMPLED rows, potentially repeated next run. They are
        # descriptive price observations, not unique trades or backtest PnL.
        for key in ("mid_buys", "expensive_buys", "buy_rows"):
            item[key + "_sampled"] = min(100000, int(item.get(key + "_sampled", 0))
                                                  + sample[key])
        item["recent_sampled_markets"] = sample["markets"]
    return added


def due_for_scoring(wallets, now):
    pending = []
    for address, item in wallets.items():
        last = int(item.get("score_checked_at") or 0)
        retry_after = int(item.get("score_retry_after") or 0)
        if now >= retry_after and (last == 0 or now - last > SCORE_REFRESH):
            pending.append((address, item))
    # Find history for newly discovered wallets before refreshing old records.
    pending.sort(key=lambda x: (-int(x[1].get("crypto_last_buy") or 0),
                                bool(x[1].get("score_checked_at")),
                                int(x[1].get("score_checked_at") or 0),
                                -int(x[1].get("mid_buys_sampled", 0)), x[0]))
    return [address for address, _ in pending[:SCORE_BATCH]]


def score_universe(state, now):
    todo = due_for_scoring(state["wallets"], now)
    success = errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(api.closed_stats, addr): addr for addr in todo}
        for future in concurrent.futures.as_completed(futures):
            address = futures[future]
            item = state["wallets"][address]
            try:
                stats = future.result()
                item.update(stats)
                item["score_checked_at"] = now
                item.pop("score_retry_after", None)
                success += 1
            except Exception as exc:
                item["score_retry_after"] = now + 3 * 3600
                errors += 1
                print("V33 SCORE ERROR:", address[:12], type(exc).__name__,
                      str(exc)[:90])
    return success, errors


def eligible_research_wallets(wallets, now, limit=250):
    candidates = []
    for address, m in wallets.items():
        if now - int(m.get("score_checked_at") or 0) > SCORE_REFRESH:
            continue
        pf = m.get("profit_factor")
        if not isinstance(pf, (int, float)) or not math.isfinite(pf) or pf < 1.15:
            continue
        if (api.asfloat(m.get("n")) < 30 or
                api.asfloat(m.get("winrate")) < .70 or
                api.asfloat(m.get("recent_realized_pnl")) < 100):
            continue
        buys = int(m.get("buy_rows_sampled") or 0)
        mid = int(m.get("mid_buys_sampled") or 0)
        expensive = int(m.get("expensive_buys_sampled") or 0)
        # Insufficient price history is marked UNVERIFIED, not a pass.
        price_supported = buys >= 4 and mid >= 2 and expensive * 2 <= buys
        candidates.append({
            "wallet": address, "name": m.get("name", ""),
            "closed": int(m["n"]), "profitable_closed_rate": m["winrate"],
            "realized_closed_pnl": m["recent_realized_pnl"],
            "profit_factor": pf,
            "sampled_buy_rows_nonunique": buys,
            "sampled_midprice_buys_nonunique": mid,
            "sampled_expensive_buys_nonunique": expensive,
            "price_profile": "candidate" if price_supported else "unverified",
            "last_market_seen": m.get("last_market_seen"),
            "last_crypto_buy": int(m.get("crypto_last_buy") or 0)})
    candidates.sort(key=lambda c: (
        not (0 <= now - int(c.get("last_crypto_buy") or 0) <= CRYPTO_ACTIVE_WINDOW),
        c["price_profile"] != "candidate",
        -min(c["sampled_midprice_buys_nonunique"], 30),
        -min(c["profit_factor"], 5),
        -c["closed"], -c["realized_closed_pnl"], c["wallet"]))
    return candidates[:limit], sum(c["price_profile"] == "candidate"
                                   for c in candidates)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, separators=(",", ":"),
                               ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def main():
    now = int(time.time())
    state = load_universe(now)
    before = len(state["wallets"])
    selected, pool_size, gamma_errors = fetch_markets(now)
    crypto_markets, crypto_gamma_errors = discover_short_crypto_markets(now)
    known = {m["condition"]: m for m in selected}
    for m in crypto_markets:
        if m["condition"] in known:
            known[m["condition"]]["crypto_short"] = True
        else:
            selected.append(m)
    if not selected:
        raise RuntimeError("No valid active markets from Gamma")
    observations = collections.defaultdict(lambda: {
        "markets": 0, "rows": 0, "mid_buys": 0,
        "expensive_buys": 0, "buy_rows": 0})
    rows_count = trade_errors = market_success = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(fetch_market_trades, m, now): m for m in selected}
        for future in concurrent.futures.as_completed(futures):
            try:
                count, wallets = future.result()
                market_success += 1
                rows_count += count
                for address, stats in wallets.items():
                    acc = observations[address]
                    acc["markets"] += 1
                    acc["crypto_last_buy"] = max(int(acc.get("crypto_last_buy") or 0),
                                                 int(stats.get("crypto_last_buy") or 0))
                    for field in ("rows", "mid_buys", "expensive_buys", "buy_rows"):
                        acc[field] += stats[field]
            except Exception as exc:
                trade_errors += 1
                print("V33 TRADE ERROR:", type(exc).__name__, str(exc)[:110])
    if market_success == 0:
        raise RuntimeError("All market trade queries failed")
    added = incorporate_sample(state, observations, now)
    scored, score_errors = score_universe(state, now)
    shortlist, price_supported = eligible_research_wallets(state["wallets"], now)
    state["runs"] = int(state.get("runs") or 0) + 1
    state["updated_at"] = now
    state["last_run"] = {
        "candidates_before": before, "candidates_now": len(state["wallets"]),
        "added": added, "target": TARGET, "gamma_markets": pool_size,
        "markets_sampled": len(selected), "market_queries_ok": market_success,
        "gamma_errors": gamma_errors, "crypto_gamma_errors": crypto_gamma_errors,
        "crypto_markets_sampled": len(crypto_markets),
        "crypto_recent_trader_wallets": sum(0 <= now-int(m.get("crypto_last_buy") or 0) <= CRYPTO_LOOKBACK for m in observations.values()),
        "crypto_qualified_in_shortlist": sum(0 <= now-int(m.get("last_crypto_buy") or 0) <= CRYPTO_ACTIVE_WINDOW for m in shortlist),
        "trade_errors": trade_errors,
        "trade_rows_sampled_nonunique": rows_count,
        "unique_wallets_seen_this_run": len(observations),
        "wallet_histories_scored": scored, "history_errors": score_errors,
        "research_shortlist_count": len(shortlist),
        "price_profile_supported_count": price_supported}
    save_json(STATE, state)
    save_json(SHORTLIST, {
        "schema": "v33_wallet_shortlist_1", "observed_at": now,
        "candidate_count": len(state["wallets"]), "target": TARGET,
        "shortlist": shortlist,
        "disclaimer": "Research-only. Trading realized PnL is not predictive win rate. "
                      "Price snapshots may repeat and cannot establish copyability. "
                      "No live monitoring, Telegram, or orders are enabled."})
    print("V33 SUMMARY:", json.dumps(state["last_run"], sort_keys=True))
    print("V33 READ-ONLY: no production state, Telegram, or orders touched")


if __name__ == "__main__":
    main()
