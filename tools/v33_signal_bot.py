#!/usr/bin/env python3
"""Standalone v3.3 research Telegram alerts from the wallet-research shortlist.

Only reads public Polymarket data. No real orders, wallet keys, or changes to
v3.1/v3.2. Closed-position win rate is NOT forecasting accuracy.
"""
import concurrent.futures
import datetime
import html
import json
import math
import os
from pathlib import Path
import re
import time

import smart_tracker_v2 as v2
import smart_tracker_v3 as v32
import paper_tracker_v31 as paper

VERSION = 33
SHORTLIST = Path("research/v33_wallet_shortlist.json")
STATE = Path("research/v33_signal_state.json")
MAX_WALLETS = max(1, min(350, int(os.getenv("V33_MONITOR_MAX", "300"))))
MAX_ALERTS = max(1, min(12, int(os.getenv("V33_MAX_ALERTS", "6"))))
REPEAT_BUY_COOLDOWN_SECONDS = 1800  # New fill on same market after 30m is new information
AGGREGATE_BUY_WINDOW = 180  # Keep short sequences of smaller BUY fills together
MIN_USD = float(os.getenv("V33_MIN_TRADE_USD", "35"))
MIN_PRICE = float(os.getenv("V33_MIN_PRICE", "0.15"))
MAX_PRICE = float(os.getenv("V33_MAX_PRICE", "0.85"))
MAX_RISE = float(os.getenv("V33_MAX_PRICE_RISE", "0.05"))
MAX_DROP = float(os.getenv("V33_MAX_PRICE_DROP", "0.05"))
MAX_SPREAD = float(os.getenv("V33_MAX_SPREAD", "0.05"))
MAX_BOOK_SLIPPAGE = float(os.getenv("V33_MAX_BOOK_SLIPPAGE", "0.02"))
EXCLUDE_POLITICS = os.getenv("V33_EXCLUDE_POLITICS", "1").strip().lower() not in ("0", "false", "no")
POLITICS_CACHE_SECONDS = 900
GAMMA_API = "https://gamma-api.polymarket.com"
POLITICS_RE = re.compile(
    r"\b(?:politic(?:s|al)?|election(?:s)?|presiden(?:t|cy|tial)|"
    r"congress|senat(?:e|or|ors)|parliament|prime.minister|"
    r"govern(?:ment|or)|mayor|referendum|impeach\w*|white.house|"
    r"supreme.court|cabinet|ballot|vot(?:e|ing|er)|"
    r"legislat(?:ion|ure)|diploma(?:cy|tic)|ceasefire|sanction(?:s|ed)?|"
    r"trump|biden|putin|zelensky|vance|macron|starmer|netanyahu)\b",
    re.I)
# Wallets supplied by the user stay at the front of a qualified shortlist.
# This never bypasses the same minimum history, PnL or risk filters.
PRIORITY_WALLETS = frozenset(w.strip().lower() for w in
    os.getenv("V33_PRIORITY_WALLETS", "").split(",")
    if v2.WALLET_RE.fullmatch(w.strip().lower()))
# Evidence-based temporary quarantine for wallets with suspicious settled losses.
# This is NOT a global ban and does not alter historical state or on-chain funds.
QUARANTINE_WALLETS = frozenset(w.strip().lower() for w in
    os.getenv("V33_QUARANTINE_WALLETS", "").split(",")
    if v2.WALLET_RE.fullmatch(w.strip().lower()))
FAST_5M_RE = re.compile(r"\b(?:btc|eth|sol|xrp)-updown-5m-(\d+)\b", re.I)
# No celebrity/movie/politics/year-out predictions in the live Telegram feed.
MARKET_FOCUS = os.getenv("V33_MARKET_FOCUS", "sports_btc").strip().lower()
CRYPTO_NAME_RE = re.compile(r"(?<![a-z0-9])(?:bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple)(?![a-z0-9])", re.I)
CRYPTO_UPDOWN_RE = re.compile(r"\b(?:btc|eth|sol|xrp)-updown-(5m|15m|1h|4h)-(\d{10})\b", re.I)
CRYPTO_WINDOW_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
MAX_MARKET_HORIZON = 24 * 3600
SPORTS_CATEGORY = re.compile(
    r"^(sports?|nba|nfl|nhl|mlb|wnba|ncaa|ncaab|ncaaf|soccer|"
    r"football|basketball|baseball|hockey|tennis|cricket|rugby|"
    r"boxing|mma|ufc|golf|pga|f1|formula.?1|premier.?league|"
    r"la.?liga|champions.?league|bundesliga|serie.?a|"
    r"ligue.?1|mls|volleyball|table.?tennis|badminton)$", re.I)
SPORTS_GAME_HORIZON = 7 * 86400
SPORTS_INPLAY_GRACE = 8 * 3600
MARKET_FOCUS_CACHE_SECONDS = 900
QUIET_STATUS_ENABLED = os.getenv("V33_QUIET_STATUS_ENABLED", "0") == "1"
CONFLICT_TELEGRAM_ENABLED = os.getenv("V33_CONFLICT_TELEGRAM_ENABLED", "0") == "1"
MAX_AGE = 300
MAX_CATCHUP = 7200
# Re-evaluate recent closed-position results at alert time, not only at
# the wallet-discovery refresh (which can be seven days old).
RECENT_CLOSED_WINDOW = 20
RECENT_PAGE_SIZE = 100
RECENT_LOST_LOOKBACK_SECONDS = 24 * 3600
RECENT_MIN_MATERIAL_LOSS_USD = float(os.getenv("V33_MIN_RECENT_LOSS_USD", "5"))
# 24h applies to last POSITION activity, not confirmed resolution time.
# Do not interpret this as the count of markets actually lost today.
RECENT_MAX_UNREDEEMED_LOSSES_24H = int(os.getenv("V33_MAX_UNREDEEMED_LOSSES_24H", "5"))
# Guard against wallets accumulating large settled-but-unredeemed losses.
# last_event_at is POSITION ACTIVITY time, not verified sports settlement.
# This is a conservative red flag, not "true sports winrate".
MAJOR_LOSS_LOOKBACK_SECONDS = 90 * 86400
MAJOR_LOSS_MIN_USD = float(os.getenv("V33_MAJOR_UNREDEEMED_LOSS_USD", "100"))
MAJOR_LOSS_MAX_90D = int(os.getenv("V33_MAX_MAJOR_LOSSES_90D", "3"))
RECENT_MIN_POSITIVE_RATE = .60
RECENT_MIN_PROFIT_FACTOR = 1.25
RECENT_MAX_LOSS_STREAK = 1
MAX_FAST_TRADE_AGE = 75
FAST_MARKET_END_BUFFER = 90
RECENT_CACHE_SECONDS = 180
# Active != closed: require a fresh, complete view of unrealized red positions.
ACTIVE_CACHE_SECONDS = 60
ACTIVE_PAGE_LIMIT = 100
ACTIVE_PAGE_CAP = 12  # up to 1,200 positions, never trust a partial portfolio
ACTIVE_MIN_ENTRY_USD = 10.0
ACTIVE_MATERIAL_LOSS_PCT = .05
ACTIVE_MAX_RED_FRACTION = float(os.getenv("V33_ACTIVE_MAX_RED_FRACTION", "0.60"))
ACTIVE_MAX_DRAWDOWN = float(os.getenv("V33_ACTIVE_MAX_DRAWDOWN", "0.15"))
QUIET_STATUS_INTERVAL = 3600  # one truthful bot-health notice/hour at most
QUOTE_STAKE = 5
DEST_RE = re.compile(r"^-?[0-9]{4,20}$|^@[A-Za-z][A-Za-z0-9_]{4,31}$")


def validate_route(env):
    new = (env.get("TELEGRAM_V33_BOT_TOKEN") or "").strip()
    old1 = (env.get("TELEGRAM_V31_BOT_TOKEN") or "").strip()
    old2 = (env.get("TELEGRAM_V32_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not new:
        raise ValueError("TELEGRAM_V33_BOT_TOKEN missing")
    if not old1 or not old2 or len({new, old1, old2}) != 3:
        raise ValueError("Bot separation not verified")
    if not DEST_RE.fullmatch(chat):
        raise ValueError("TELEGRAM_CHAT_ID missing or malformed")
    os.environ["TELEGRAM_BOT_TOKEN"] = new
    os.environ["TELEGRAM_CHAT_ID"] = chat


def select_wallets(shortlist, now):
    if shortlist.get("schema") != "v33_wallet_shortlist_1":
        raise ValueError("Invalid v3.3 shortlist schema")
    if now - int(shortlist.get("observed_at") or 0) > 3 * 86400:
        raise ValueError("Shortlist older than 3 days")
    selected = {}
    qualified = []
    for meta in shortlist.get("shortlist", []):
        if not isinstance(meta, dict):
            continue
        wallet = str(meta.get("wallet") or "").lower()
        if (not v2.WALLET_RE.fullmatch(wallet) or wallet in QUARANTINE_WALLETS
                or any(w == wallet for w, _ in qualified)):
            continue
        if (v2.asfloat(meta.get("closed")) < 30 or
                v2.asfloat(meta.get("profitable_closed_rate")) < 0.70 or
                v2.asfloat(meta.get("realized_closed_pnl")) < 100 or
                v2.asfloat(meta.get("profit_factor")) < 1.15):
            continue
        qualified.append((wallet, meta))
    # Python sort is stable: retain researcher ranking within each priority tier.
    qualified.sort(key=lambda item: item[0] not in PRIORITY_WALLETS)
    for wallet, meta in qualified[:MAX_WALLETS]:
        selected[wallet] = {"name": str(meta.get("name") or "")[:60],
                            "winrate": v2.asfloat(meta["profitable_closed_rate"]),
                            "profit_factor": v2.asfloat(meta["profit_factor"]),
                            "price_profile": meta.get("price_profile"),
                            "last_crypto_buy": int(meta.get("last_crypto_buy") or 0),
                            "last_sports_buy": int(meta.get("last_sports_buy") or 0)}
    if not selected:
        raise RuntimeError("No qualified wallets")
    return selected


def blank_state():
    return {"version": VERSION, "cursors": {}, "seen": {},
            "pending": {}, "sent": {}, "counters": {},
            "recent_form_cache": {}, "active_form_cache": {}, "politics_cache": {},
            "market_focus_cache": {}, "signal_evidence": {},
            "shadow_evidence": {},
            "conflicting_markets": {}, "conflict_alerts": {},
            "boot_notified": False}


def load_state():
    if not STATE.exists():
        return blank_state()
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if value.get("version") != VERSION:
        raise ValueError("Incompatible v3.3 state")
    for k in ("cursors", "seen", "pending", "sent", "counters"):
        if not isinstance(value.get(k), dict):
            raise ValueError("Corrupted v3.3 state: " + k)
    if not isinstance(value.setdefault("recent_form_cache", {}), dict):
        raise ValueError("Corrupted v3.3 recent form cache")
    if not isinstance(value.setdefault("active_form_cache", {}), dict):
        raise ValueError("Corrupted v3.3 active form cache")
    if not isinstance(value.setdefault("politics_cache", {}), dict):
        raise ValueError("Corrupted v3.3 politics cache")
    if not isinstance(value.setdefault("market_focus_cache", {}), dict):
        raise ValueError("Corrupted v3.3 market focus cache")
    if not isinstance(value.setdefault("conflicting_markets", {}), dict):
        raise ValueError("Corrupted v3.3 conflict cache")
    if not isinstance(value.setdefault("conflict_alerts", {}), dict):
        raise ValueError("Corrupted v3.3 conflict alerts")
    if not isinstance(value.setdefault("signal_evidence", {}), dict):
        raise ValueError("Corrupted v3.3 signal evidence")
    if not isinstance(value.setdefault("shadow_evidence", {}), dict):
        raise ValueError("Corrupted v3.3 shadow evidence")
    return value


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False,
                               separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(STATE)


def fetch_recent_results(wallet, now):
    """Read v2 CLOSED history AND settled-but-unredeemed losses.

    The legacy /closed-positions route omits some resolved losing holdings.
    v2 REDEEMABLE_LOST explicitly includes those losses; its last_event_at
    denotes LAST POSITION ACTIVITY, not necessarily market resolution time.
    We do not represent this as the Polymarket UI's exact chronological streak.
    """
    data = []
    for status in ("CLOSED", "REDEEMABLE_LOST"):
        # CLOSED is used only for the newest 20 recent PnL entries.
        # Settled zero-payout holdings require a fuller scan: a wallet with
        # hundreds of old unredeemed losses must not look safe by accident.
        pages = 4 if status == "REDEEMABLE_LOST" else 1
        cursor = None
        used = set()
        for page_number in range(pages):
            params = {
                "user": wallet, "status": status,
                "sort_by": "TIMESTAMP", "sort_direction": "DESC",
                "limit": 250 if status == "REDEEMABLE_LOST" else RECENT_PAGE_SIZE,
            }
            if status == "REDEEMABLE_LOST":
                # The website's Active tab includes still-held settled losses.
                params["include_archived"] = "true"
            if cursor:
                params["cursor"] = cursor
            response = v2.request_json("/v2/positions", params,
                                       tries=2, timeout=12)
            if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                raise ValueError("Incomplete v2 recent positions response")
            pagination = response.get("pagination")
            if not isinstance(pagination, dict):
                raise ValueError("Unknown v2 recent positions pagination")
            page = response["data"]
            if any(not isinstance(item, dict) for item in page):
                raise ValueError("Malformed v2 recent position")
            for item in page:
                if str(item.get("proxy_wallet") or "").lower() != wallet:
                    raise ValueError("Position wallet identity mismatch")
                condition = str(item.get("condition_id") or "").lower()
                token = str(item.get("token_id") or "")
                if not v32.discovery.CONDITION_RE.fullmatch(condition) or not token:
                    raise ValueError("Position lacks condition or outcome identity")
                timestamp = v2.asfloat(item.get("last_event_at"), float("nan"))
                if not math.isfinite(timestamp) or timestamp <= 0 or timestamp > now + 60:
                    raise ValueError("Position activity timestamp invalid")
                if status == "CLOSED":
                    if item.get("status") != "CLOSED":
                        raise ValueError("Expected closed position")
                    pnl = v2.asfloat(item.get("realized_pnl"), float("nan"))
                else:
                    if item.get("status") != "REDEEMABLE" or item.get("redeemable") is not True:
                        raise ValueError("Cannot verify settled losing position")
                    realized = v2.asfloat(item.get("realized_pnl"), float("nan"))
                    unrealized = v2.asfloat(item.get("unrealized_pnl"), float("nan"))
                    pnl = realized + unrealized
                    if pnl >= -0.01:
                        continue  # the API can return unrelated redeemables
                if not math.isfinite(pnl):
                    raise ValueError("Missing/nonfinite recent position PnL")
                data.append({
                    "realizedPnl": pnl, "timestamp": int(timestamp),
                    "condition_id": condition, "token_id": token,
                    "_unredeemed_lost": status == "REDEEMABLE_LOST",
                })
            # The CLOSED top 100 are sufficient for recent 20, but a
            # truncated REDEEMABLE_LOST risk inventory is never a clean bill.
            if status == "CLOSED":
                break
            if pagination.get("has_more") is False:
                break
            nxt = pagination.get("next_cursor")
            if (pagination.get("has_more") is not True or
                    not isinstance(nxt, str) or not nxt or nxt in used):
                raise ValueError("Incomplete/invalid settled-loss pagination")
            used.add(nxt)
            cursor = nxt
        else:
            if status == "REDEEMABLE_LOST":
                raise RuntimeError("Settled-loss history incomplete after 4 pages")
    # Never count one position twice. Preserve the worst available result
    # if an inconsistent API snapshot claims both CLOSED and REDEEMABLE.
    deduped = {}
    for item in data:
        key = (item["condition_id"], item["token_id"])
        old = deduped.get(key)
        if old is None or item["realizedPnl"] < old["realizedPnl"]:
            deduped[key] = item
    return sorted(deduped.values(), key=lambda row: -row["timestamp"])


def recent_form(rows, now=None):
    """Recent position PnL, including settled unredeemed losers.

    v2 last_event_at is LAST POSITION ACTIVITY, not necessarily market
    resolution time. The calculated streak is therefore an activity-ordered
    risk proxy, not an exact replica of the website's results timeline.
    """
    if not isinstance(rows, list):
        raise ValueError("Recent closed positions API returned non-list")
    subset = rows[:RECENT_CLOSED_WINDOW]
    if any(not isinstance(x, dict) for x in subset):
        raise ValueError("Recent closed positions contain malformed entries")
    if any(x.get("realizedPnl") is None for x in subset):
        raise ValueError("Missing recent realized PnL")
    pnls = [v2.asfloat(x.get("realizedPnl"), float("nan")) for x in subset]
    if not all(math.isfinite(p) for p in pnls):
        raise ValueError("Nonfinite recent realized PnL")
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    streak = 0
    for pnl in pnls:
        if pnl >= 0:
            break
        streak += 1
    recent_unredeemed_losses = sum(
        bool(x.get("_unredeemed_lost")) and
        x.get("realizedPnl", 0.) <= -RECENT_MIN_MATERIAL_LOSS_USD and
        0 <= now - int(x.get("timestamp") or 0) <= RECENT_LOST_LOOKBACK_SECONDS
        for x in rows
    ) if now is not None else 0
    major_unredeemed_90d = sum(
        bool(x.get("_unredeemed_lost")) and
        x.get("realizedPnl", 0.) <= -MAJOR_LOSS_MIN_USD and
        0 <= now - int(x.get("timestamp") or 0) <= MAJOR_LOSS_LOOKBACK_SECONDS
        for x in rows
    ) if now is not None else 0
    last_five = pnls[:5]
    return {"closed": len(pnls),
            "positive_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else 0.,
            "loss_streak": streak, "net_pnl": gains - losses,
            "last5_wins": sum(p > 0 for p in last_five),
            "last5_net_pnl": sum(last_five),
            "profit_factor": gains / losses if losses > 0 else None,
            "unredeemed_lost_24h": recent_unredeemed_losses,
             "major_unredeemed_lost_90d": major_unredeemed_90d}


def recent_form_reason(state, wallet, now):
    """Fail closed on poor or unknown recent performance; cache briefly."""
    cache = state.setdefault("recent_form_cache", {})
    value = cache.get(wallet)
    if not (isinstance(value, dict) and
            "profit_factor" in value and "net_pnl" in value and
            "last5_net_pnl" in value and "last5_wins" in value and
            "unredeemed_lost_24h" in value and
            "major_unredeemed_lost_90d" in value and
            0 <= now - int(value.get("checked_at") or 0) <= RECENT_CACHE_SECONDS):
        rows = fetch_recent_results(wallet, now)
        value = recent_form(rows, now)
        value["checked_at"] = now
        cache[wallet] = value
    if value["unredeemed_lost_24h"] > RECENT_MAX_UNREDEEMED_LOSSES_24H:
        return "recent_unredeemed_loss_cluster"
    if value["major_unredeemed_lost_90d"] > MAJOR_LOSS_MAX_90D:
        return "major_unredeemed_loss_cluster_90d"
    if value["closed"] < RECENT_CLOSED_WINDOW:
        return "recent_sample_too_small"
    if value["loss_streak"] > RECENT_MAX_LOSS_STREAK:
        return "recent_loss_streak"
    if value["positive_rate"] < RECENT_MIN_POSITIVE_RATE:
        return "recent_positive_rate_low"
    if value["last5_wins"] < 3 or value["last5_net_pnl"] <= 0:
        return "recent_last5_poor"
    if (value["net_pnl"] <= 0 or
            (value["profit_factor"] is not None and
             value["profit_factor"] < RECENT_MIN_PROFIT_FACTOR)):
        return "recent_negative_expectancy_sample"
    return None


def fetch_open_positions(wallet):
    """Fetch the WHOLE open portfolio, not just the current signal's market.

    v2/positions OPEN can include redeemable holdings; those are not live
    active positions and are deliberately excluded from drawdown scoring.
    Never claim good active form from a truncated / partial API response.
    """
    cursor = None
    seen_cursors = set()
    rows = []
    for page_number in range(1, ACTIVE_PAGE_CAP + 1):
        params = {"user": wallet, "status": "OPEN",
                  "filter_type": "TOKENS", "filter_amount": "0.01",
                  "sort_by": "CURRENT_VALUE", "sort_direction": "DESC",
                  "limit": ACTIVE_PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        response = v2.request_json("/v2/positions", params, tries=2, timeout=12)
        if not (isinstance(response, dict) and
                isinstance(response.get("data"), list) and
                isinstance(response.get("pagination"), dict)):
            raise ValueError("Malformed active positions API response")
        page = response["data"]
        if any(not isinstance(x, dict) for x in page):
            raise ValueError("Malformed active position row")
        rows.extend(page)
        pagination = response["pagination"]
        if pagination.get("has_more") is False:
            if page_number > 5:
                print("V33 ACTIVE SNAPSHOT COMPLETE:", page_number, "pages")
            return rows
        if pagination.get("has_more") is not True:
            raise ValueError("Active positions pagination status unknown")
        new_cursor = pagination.get("next_cursor")
        if (not isinstance(new_cursor, str) or not new_cursor or
                new_cursor == cursor or new_cursor in seen_cursors):
            raise ValueError("Active positions pagination cursor invalid")
        seen_cursors.add(new_cursor)
        cursor = new_cursor
    raise RuntimeError(
        "Active positions page cap: incomplete wallet snapshot "
        f"after {ACTIVE_PAGE_CAP} pages")


def active_form(rows):
    """Unrealized PnL of live held positions, not finalized trade results."""
    if not isinstance(rows, list):
        raise ValueError("Invalid active position list")
    checked = 0
    red = 0
    total_cost = 0.0
    unrealized = 0.0
    for pos in rows:
        if not isinstance(pos, dict):
            raise ValueError("Invalid active position row")
        # OPEN is broader than live holdings: exclude resolved/redeemable.
        if pos.get("redeemable") is True or pos.get("status") == "REDEEMABLE":
            continue
        if pos.get("status") != "OPEN" or pos.get("redeemable") is not False:
            raise ValueError("Unknown active position status")
        size = v2.asfloat(pos.get("current_size"), float("nan"))
        if not math.isfinite(size):
            raise ValueError("Unknown current size")
        if size < .01:
            continue
        cost = v2.asfloat(pos.get("entry_cost_usdc"), float("nan"))
        pnl = v2.asfloat(pos.get("unrealized_pnl"), float("nan"))
        if not math.isfinite(cost) or not math.isfinite(pnl):
            raise ValueError("Unknown active PnL or cost")
        if cost <= 0:
            raise ValueError("Invalid active entry cost")
        if cost < ACTIVE_MIN_ENTRY_USD:
            continue  # exclude dust and negligible exposures
        checked += 1
        total_cost += cost
        unrealized += pnl
        red += int(pnl <= -ACTIVE_MATERIAL_LOSS_PCT * cost)
    roi = unrealized / total_cost if total_cost else 0.
    return {"active_count": checked, "red_count": red,
            "red_fraction": red / checked if checked else 0.,
            "unrealized_pnl": unrealized, "active_cost": total_cost,
            "unrealized_roi": roi}


def active_form_reason(state, wallet, now):
    """Protect against wallets with many losing ACTIVE positions."""
    cache = state.setdefault("active_form_cache", {})
    result = cache.get(wallet)
    if not (isinstance(result, dict) and
            "unrealized_roi" in result and "red_fraction" in result and
            0 <= now - int(result.get("checked_at") or 0) <= ACTIVE_CACHE_SECONDS):
        result = active_form(fetch_open_positions(wallet))
        result["checked_at"] = now
        cache[wallet] = result
    # Three or more materially losing holdings, or most exposures red.
    if (result["active_count"] >= 3 and
            result["red_count"] >= 2 and
            result["red_fraction"] >= ACTIVE_MAX_RED_FRACTION):
        return "active_most_positions_red"
    # Also reject deep account-wide active drawdowns (including few positions).
    if (result["active_cost"] >= 25 and
            result["unrealized_roi"] <= -ACTIVE_MAX_DRAWDOWN):
        return "active_portfolio_drawdown"
    return None


def mark(state, reason):
    x = state["counters"]
    x[reason] = int(x.get(reason) or 0) + 1


def add_fill(state, row, now):
    wallet = str(row.get("proxy_wallet") or "").lower()
    condition = str(row.get("condition_id") or "").lower()
    token = str(row.get("token_id") or "")
    side = row.get("side")
    stamp = int(v2.asfloat(row.get("timestamp")))
    if not (v2.WALLET_RE.fullmatch(wallet) and
            v32.discovery.CONDITION_RE.fullmatch(condition) and token and
            side in ("BUY", "SELL") and now-MAX_CATCHUP <= stamp <= now+60):
        return False
    digest = v32.trade_id(row)
    if digest in state["seen"]:
        return False
    state["seen"][digest] = now
    key = wallet + "|" + condition
    # A stream of $10/$20 fills may exceed the threshold together. Retain
    # short sequences, but never combine buys stretching across long periods.
    old_group = state["pending"].get(key)
    if old_group and stamp - int(old_group.get("first", old_group["last"])) > AGGREGATE_BUY_WINDOW:
        del state["pending"][key]
    group = state["pending"].setdefault(key, {
        "wallet": wallet, "condition": condition, "first": stamp, "last": stamp,
        "title": str(row.get("title") or "Market")[:160],
        "url": v32.slug_link(row), "outcomes": {}})
    group["last"] = max(group["last"], stamp)
    if row.get("title"):
        group["title"] = str(row["title"])[:160]
    if v32.slug_link(row):
        group["url"] = v32.slug_link(row)
    outcome = group["outcomes"].setdefault(token, {
        "label": str(row.get("outcome") or "?")[:70],
        "buy_usd": 0., "buy_qty": 0., "sell_usd": 0., "sell_qty": 0.})
    size = max(0., v2.asfloat(row.get("size")))
    price = v2.asfloat(row.get("price"))
    usd = size * price if 0 < price < 1 else 0.
    if side == "BUY":
        outcome["buy_usd"] += usd
        outcome["buy_qty"] += size
    else:
        outcome["sell_usd"] += usd
        outcome["sell_qty"] += size
    return True


def candidate(group, now):
    if now - group["last"] > MAX_AGE:
        return None, "expired"
    buys = [(t, p) for t, p in group["outcomes"].items() if p["buy_qty"] > 0]
    if len(buys) != 1 or any(p["sell_qty"] > 0 for p in group["outcomes"].values()):
        return None, "mixed_or_sell"
    token, pos = buys[0]
    if pos["buy_qty"] <= 0 or pos["buy_usd"] < MIN_USD:
        return None, "small_trade"
    source = pos["buy_usd"] / pos["buy_qty"]
    if not MIN_PRICE <= source <= MAX_PRICE:
        return None, "source_price_excluded"
    return {"token": token, "source": source, "usd": pos["buy_usd"],
            "outcome": pos["label"]}, None


def fast_market_reason(group, now):
    """Short crypto markets need fresh fills and time for a human to act."""
    m = FAST_5M_RE.search(group.get("url") or "")
    if m is None:
        return None
    start = int(m.group(1))
    if start < 1600000000 or start > 2100000000:
        return "fast_market_bad_timestamp"
    if now < start - 15 or now > start + 300 - FAST_MARKET_END_BUFFER:
        return "fast_market_too_late"
    if now - int(group["last"]) > MAX_FAST_TRADE_AGE:
        return "fast_trade_stale"
    return None


def official_sport_market(market):
    """Require official Gamma game data or a sports category/league tag."""
    if not isinstance(market, dict):
        return False
    if market.get("sportsMarketType") or market.get("gameId"):
        return True
    for value in (market.get("category"), market.get("subcategory")):
        if SPORTS_CATEGORY.fullmatch(str(value or "").strip()):
            return True
    for key in ("tags", "categories"):
        tags = market.get(key) or []
        if isinstance(tags, dict):
            tags = [tags]
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if isinstance(tag, dict):
                if any(SPORTS_CATEGORY.fullmatch(str(tag.get(k) or "").strip())
                       for k in ("label", "slug", "name")):
                    return True
            elif SPORTS_CATEGORY.fullmatch(str(tag).strip()):
                return True
    return False


def gamma_epoch(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp()) if dt.tzinfo else None
    except (ValueError, OverflowError):
        return None


def is_btc_market(group):
    """Recognize Bitcoin labels, not sufficient on its own for topic approval."""
    label = str(group.get("title") or "") + " " + str(group.get("url") or "")
    return bool(re.search(r"(?<![a-z0-9])(?:bitcoin|btc)(?![a-z0-9])",
                          label, re.I))


def market_focus_reason(state, group, now):
    """Allow only demonstrably short-horizon BTC/ETH/SOL/XRP markets.

    The early whitelist rejects entertainment/future-year content before
    expensive API verification; non-standard crypto markets need an official
    Gamma close time within 24h, never just a suggestive question/title.
    """
    if MARKET_FOCUS == "all":
        return None
    if MARKET_FOCUS not in ("crypto_short", "sports_btc"):
        raise ValueError("Unsupported v3.3 market focus")
    label = str(group.get("title") or "") + " " + str(group.get("url") or "")
    # The production sports-first mode also supports Bitcoin, but no altcoins.
    is_btc = is_btc_market(group)
    if MARKET_FOCUS == "crypto_short" and not CRYPTO_NAME_RE.search(label):
        return "off_topic_market"
    if MARKET_FOCUS == "sports_btc" and re.search(
            r"(?<![a-z0-9])(?:ethereum|eth|solana|sol|xrp|ripple)(?![a-z0-9])",
            label, re.I):
        return "off_topic_market"
    match = CRYPTO_UPDOWN_RE.search(str(group.get("url") or ""))
    if match:
        if MARKET_FOCUS == "sports_btc" and not match.group(0).lower().startswith("btc-"):
            return "off_topic_market"
        start = int(match.group(2))
        if not 1600000000 <= start <= 2100000000:
            return "market_bad_timestamp"
        remaining = start + CRYPTO_WINDOW_SECONDS[match.group(1).lower()] - now
        if remaining <= 90:
            return "crypto_market_too_late"
        if remaining > MAX_MARKET_HORIZON:
            return "crypto_market_too_far"
        return None
    condition = str(group.get("condition") or "").lower()
    cache = state.setdefault("market_focus_cache", {})
    record = cache.get(condition)
    if not (isinstance(record, dict) and record.get("focus") == MARKET_FOCUS and
            0 <= now - int(record.get("checked_at") or 0) <= MARKET_FOCUS_CACHE_SECONDS):
        markets = v2.request_json("/markets", {"condition_ids": condition},
                                  base=GAMMA_API, tries=2, timeout=12)
        if not isinstance(markets, list):
            raise ValueError("Gamma market metadata unavailable")
        matches = [m for m in markets if isinstance(m, dict) and
                   str(m.get("conditionId") or "").lower() == condition]
        if len(matches) != 1:
            raise ValueError("Missing/ambiguous Gamma market metadata")
        market = matches[0]
        events = [e for e in (market.get("events") or []) if isinstance(e, dict)]
        sports = official_sport_market(market) or any(
            official_sport_market(e) for e in events)
        start = gamma_epoch(market.get("gameStartTime") or market.get("eventStartTime"))
        if start is None:
            for event in events:
                start = gamma_epoch(event.get("gameStartTime") or event.get("eventStartTime"))
                if start is not None:
                    break
        end = gamma_epoch(market.get("endDate") or market.get("endDateIso"))
        record = {"checked_at": now, "focus": MARKET_FOCUS,
                  "sports": sports, "game_start": start, "end": end}
        cache[condition] = record
    if MARKET_FOCUS == "sports_btc" and not is_btc:
        if not record["sports"]:
            return "off_topic_market"
        # endDate on sports may denote kickoff rather than settlement.
        when = record.get("game_start") or record.get("end")
        if not isinstance(when, (int, float)):
            return "sports_time_unverified"
        if when - now > SPORTS_GAME_HORIZON:
            return "sports_game_too_far"
        if now - when > SPORTS_INPLAY_GRACE:
            return "sports_game_stale"
        return None
    end = record.get("end")
    if not isinstance(end, (int, float)):
        return "market_end_unverified"
    if end - now <= 90:
        return "crypto_market_too_late"
    if end - now > MAX_MARKET_HORIZON:
        return "crypto_market_too_far"
    return None


def political_labels(obj):
    """Classify event/market category, tags and public market text."""
    if not isinstance(obj, dict):
        return False
    for field in ("category", "subcategory", "label", "question", "title", "slug"):
        if POLITICS_RE.search(str(obj.get(field) or "").replace("-", " ")):
            return True
    for field in ("tags", "categories"):
        raw = obj.get(field) or []
        if isinstance(raw, dict):
            raw = [raw]
        if isinstance(raw, list):
            for tag in raw:
                if isinstance(tag, dict):
                    if political_labels({k: tag.get(k) for k in ("label", "slug", "title", "category")}):
                        return True
                elif POLITICS_RE.search(str(tag).replace("-", " ")):
                    return True
    return False


def political_market_reason(state, group, now):
    """Consult official Gamma market and event labels before any Telegram alert.

    Never accept unknown metadata as proven nonpolitical. Cache by condition,
    since many watched wallets can trade the same event simultaneously.
    """
    if not EXCLUDE_POLITICS:
        return None
    if (POLITICS_RE.search(str(group.get("title") or "")) or
            POLITICS_RE.search(str(group.get("url") or "").replace("-", " "))):
        return "political_market"
    condition = str(group["condition"]).lower()
    cache = state.setdefault("politics_cache", {})
    record = cache.get(condition)
    if (isinstance(record, dict) and
            0 <= now - int(record.get("checked_at") or 0) <= POLITICS_CACHE_SECONDS and
            isinstance(record.get("political"), bool)):
        return "political_market" if record["political"] else None
    markets = v2.request_json("/markets", {"condition_ids": condition},
                              base=GAMMA_API, tries=2, timeout=12)
    if not isinstance(markets, list):
        raise ValueError("Unexpected Gamma market metadata")
    matches = [m for m in markets if isinstance(m, dict) and
               str(m.get("conditionId") or "").lower() == condition]
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous Gamma market metadata")
    market = matches[0]
    political = political_labels(market)
    for event in market.get("events") or []:
        political = political or political_labels(event)
    # Event tags are authoritative when market fields omit its category.
    slug = str(group.get("url") or "").split("/event/")[-1]
    if "/event/" not in str(group.get("url") or ""):
        slug = ""
    if not slug and market.get("events"):
        slug = str(market["events"][0].get("slug") or "")
    if not political and slug and re.fullmatch(r"[A-Za-z0-9_-]{1,220}", slug):
        events = v2.request_json("/events", {"slug": slug},
                                 base=GAMMA_API, tries=2, timeout=12)
        if not isinstance(events, list) or not events:
            raise ValueError("Gamma event category could not be verified")
        matched = [e for e in events if isinstance(e, dict) and e.get("slug") == slug]
        if len(matched) != 1:
            raise ValueError("Gamma event slug mismatch")
        political = political_labels(matched[0])
    elif not political and not slug and not market.get("tags"):
        # No category, no event identity: cannot verify this isn't politics.
        raise ValueError("Market category missing; politics filter unavailable")
    cache[condition] = {"checked_at": now, "political": political}
    return "political_market" if political else None


def check_trade(group, cand):
    if not v32.market_accepts_orders(group["condition"]):
        return None, "market_closed"
    holdings = v32.get_positions(
        group["wallet"], {group["condition"]}).get(group["condition"], {})
    if holdings.get(cand["token"], 0) < 0.01:
        return None, "position_not_held"
    if any(v >= 0.01 for t, v in holdings.items() if t != cand["token"]):
        return None, "opposite_outcome_held"
    quote = paper.book_simulation(cand["token"], QUOTE_STAKE)
    if not quote:
        return None, "book_no_liquidity"
    if quote.get("one_dollar_meets_min") is not True:
        return None, "minimum_order_not_verified"
    ask = quote["price"]
    if not MIN_PRICE <= ask <= MAX_PRICE:
        return None, "ask_price_excluded"
    if cand["source"] - ask > MAX_DROP:
        return None, "price_fell_sharply"
    if ask - cand["source"] > MAX_RISE or ask / cand["source"] > 1.2:
        return None, "price_moved"
    # A tradable $5 ask is not sufficient if selling back is expensive.
    if (quote.get("spread") is None or quote["spread"] < 0 or
            quote["spread"] > MAX_SPREAD):
        return None, "spread_too_wide"
    if ask - quote["best_ask"] > MAX_BOOK_SLIPPAGE:
        return None, "book_slippage"
    return quote, None


def message(group, meta, cand, quote, now, recent=None, active=None):
    name = html.escape(meta["name"] or group["wallet"][:12])
    url = html.escape(group.get("url") or "", quote=True)
    link = f'<a href="https://polymarket.com/profile/{group["wallet"]}">Кошелёк</a>'
    if url:
        link = f'<a href="{url}">Рынок</a> | ' + link
    recent_note = ""
    active_note = ""
    size_note = ("ℹ️ BUY меньше $50: меньший объём исходного трейда.\n"
                 if cand["usd"] < 50 else "")
    if active:
        active_note = (
            f"\U0001f4ca LIVE (без рассчитанных): {active['active_count']} позиций, "
            f"красных {active['red_count']}; "
            f"нереализованный PnL {active['unrealized_pnl']:+,.0f}$\n")
    if recent:
        recent_note = (f"\U0001f4c9 Последние {recent['closed']} учтённых позиций: "
                       f"{recent['positive_rate']:.0%} прибыльных; "
                       f"лузов подряд: {recent['loss_streak']}; "
                       f"PnL: ${recent['net_pnl']:+,.0f}; "
            f"последние 5: {recent['last5_wins']}/5 прибыльных, "
            f"PnL ${recent['last5_net_pnl']:+,.0f}; "
            f"непогашенных убыточных позиций с активностью за 24ч: "
            f"{recent['unredeemed_lost_24h']}; "
            f"крупных непогашенных убытков с активностью за 90д: "
            f"{recent.get('major_unredeemed_lost_90d', '?')}\n"
            "⚠️ Это PnL записей позиций по времени действий, "
            "НЕ процент угаданных спортивных матчей.\n")
    return ("\U0001f9ea <b>v3.3 • новый BUY-трейд</b>\n"
            + "\U0001f464 " + name + "\n"
            + "\U0001f3f7 " + html.escape(group["title"]) + "\n"
            + "\U0001f3af " + html.escape(cand["outcome"]) + "\n"
            + f"\U0001f4b5 Трейд: \u0024{cand['usd']:,.0f} по {cand['source']:.3f}\n"
            + size_note
            + f"\U0001f4ca Ask: ~{quote['price']:.3f} для гипотетических "
              f"\u0024{QUOTE_STAKE:g}; задержка {(now-group['last'])//60} мин\n"
            + f"\U0001f4c8 Прибыльные закрытые позиции: {meta['winrate']:.0%} "
              "(не точность прогнозов)\n" + recent_note + active_note + link + "\n"
            + "\u26a0\ufe0f Исследовательский сигнал, не гарантия прибыли.")


def signal_sort_key(item):
    """In sports/BTC mode, place sports ahead of BTC, then honor wallet priority.

    A non-BTC title is NOT enough to pass topic validation: deliver() still
    requires official Gamma sports metadata before any BUY can be sent.
    """
    group = item[1]
    return (MARKET_FOCUS == "sports_btc" and is_btc_market(group),
            group["wallet"] not in PRIORITY_WALLETS,
            FAST_5M_RE.search(group.get("url") or "") is None,
            -int(group["last"]))


def conflicting_wallet_signal(state, group, cand, now):
    """Avoid blindly copy-alerting opposing watched wallets in one market.

    Another eligible BUY in the same market on a different token, or a
    conflicting alert sent in the last 5 minutes, makes direction ambiguous.
    This does not establish which side is correct.
    """
    condition = group["condition"]
    wallet = group["wallet"]
    conflicts = state.setdefault("conflicting_markets", {})
    if 0 <= now - int(conflicts.get(condition) or 0) <= MAX_AGE:
        return True
    for other in state["pending"].values():
        if other is group or other["condition"] != condition:
            continue
        if other["wallet"] == wallet:
            continue
        other_cand, other_reason = candidate(other, now)
        if other_reason is None and other_cand["token"] != cand["token"]:
            conflicts[condition] = now
            return True
    for sent_key, sent_at in state["sent"].items():
        if now - int(sent_at) > MAX_AGE or int(sent_at) > now + 60:
            continue
        parts = sent_key.split("|")
        if len(parts) != 3:
            continue
        old_wallet, old_condition, old_token = parts
        if (old_wallet != wallet and old_condition == condition and
                old_token != cand["token"]):
            conflicts[condition] = now
            return True
    return False


def notify_opposing_wallets(state, selected, now):
    """Report opposing fresh BUY directions as a WARNING, never copy advice.

    Matching wallet trades do NOT assert either is profitable or executable.
    Signals are verified separately and remain suppressed by the conflict gate.
    """
    by_market = {}
    for group in state["pending"].values():
        if group["wallet"] not in selected or fast_market_reason(group, now):
            continue
        try:
            if market_focus_reason(state, group, now):
                continue
        except Exception as exc:
            mark(state, "market_focus_request_error")
            print("V33 MARKET FOCUS WARNING:", type(exc).__name__, str(exc)[:80])
            continue
        cand, reason = candidate(group, now)
        if reason is None:
            by_market.setdefault(group["condition"], []).append((group, cand))
    prior = state.setdefault("conflict_alerts", {})
    warnings = 0
    for condition, group_candidates in by_market.items():
        if warnings >= 3:
            break
        if 0 <= now - int(prior.get(condition) or 0) <= MAX_AGE:
            continue
        a = b = None
        for idx, first in enumerate(group_candidates):
            for second in group_candidates[idx + 1:]:
                if (first[0]["wallet"] != second[0]["wallet"] and
                        first[1]["token"] != second[1]["token"]):
                    a, b = first, second
                    break
            if a is not None:
                break
        if a is None:
            continue
        try:
            if political_market_reason(state, a[0], now):
                continue
        except Exception as exc:
            mark(state, "conflict_category_error")
            print("V33 CONFLICT WARNING:", type(exc).__name__, str(exc)[:100])
            continue
        group_a, cand_a = a
        group_b, cand_b = b
        slug = html.escape(group_a.get("url") or "", quote=True)
        url = f'<a href="{slug}">Рынок</a>\n' if slug else ""
        msg = ("⚠️ <b>v3.3 • КОНФЛИКТ кошельков</b>\n"
               + "🏷 " + html.escape(group_a["title"]) + "\n"
               + "🔴 " + html.escape(group_a["wallet"][:12]) + " → "
                 + html.escape(cand_a["outcome"]) + f" (${cand_a['usd']:,.0f})\n"
               + "🔵 " + html.escape(group_b["wallet"][:12]) + " → "
                 + html.escape(cand_b["outcome"]) + f" (${cand_b['usd']:,.0f})\n"
               + url
               + "⛔ Противоположные BUY наблюдались в одном рынке. "
                 "Ликвидность и копируемость этих трейдов НЕ подтверждены. "
                 "Не копировать автоматически.")
        try:
            v2.send_telegram(msg)
        except Exception as exc:
            mark(state, "conflict_telegram_error")
            print("V33 CONFLICT TELEGRAM WARNING:", type(exc).__name__,
                  str(exc)[:100])
            continue
        prior[condition] = now
        state.setdefault("conflicting_markets", {})[condition] = now
        mark(state, "conflict_alerts_sent")
        warnings += 1
    return warnings


def deliver(state, selected, now, *, clock=None):
    """Only send verified fresh fills; optionally use a live clock per stage."""
    sent = 0
    for key, group in sorted(list(state["pending"].items()),
                             key=signal_sort_key):
        checked_at = max(now, int(clock())) if clock else now
        try:
            focus_reason = market_focus_reason(state, group, checked_at)
        except Exception as exc:
            mark(state, "market_focus_request_error")
            print("V33 MARKET FOCUS WARNING:", type(exc).__name__, str(exc)[:80])
            continue
        if focus_reason:
            mark(state, focus_reason)
            del state["pending"][key]
            continue
        cand, reason = candidate(group, checked_at)
        if reason == "small_trade" and (checked_at - int(
                group.get("first", group["last"])) <= AGGREGATE_BUY_WINDOW):
            # Keep recent sub-threshold fills for the next poll: $20+$20
            # is actionable information only if close together in time.
            continue
        if reason:
            mark(state, reason)
            del state["pending"][key]
            continue
        if group["wallet"] not in selected:
            mark(state, "wallet_not_selected")
            del state["pending"][key]
            continue
        dedupe = key + "|" + cand["token"]
        # A later, NEW purchase on the same outcome can be useful. The
        # individual fill IDs in state["seen"] still prevent replays.
        if checked_at - int(state["sent"].get(dedupe) or 0) < REPEAT_BUY_COOLDOWN_SECONDS:
            del state["pending"][key]
            continue
        if sent >= MAX_ALERTS:
            mark(state, "alert_cap")
            continue
        # These counts start at deployment, do not measure independent trades
        # or profitability; each passed gate is explicit and fail-closed.
        topic = ("btc" if is_btc_market(group) else "sports") if (
            MARKET_FOCUS == "sports_btc") else None
        if topic:
            mark(state, "eligible_source_buy_" + topic)
        if conflicting_wallet_signal(state, group, cand, checked_at):
            mark(state, "conflicting_wallets")
            del state["pending"][key]
            continue
        timing_reason = fast_market_reason(group, checked_at)
        if timing_reason:
            mark(state, timing_reason)
            del state["pending"][key]
            continue
        try:
            politics_reason = political_market_reason(state, group, checked_at)
        except Exception as exc:
            mark(state, "politics_metadata_error")
            print("V33 POLITICS WARNING:", type(exc).__name__, str(exc)[:100])
            continue  # fail closed; do not send unclassified markets
        if politics_reason:
            mark(state, politics_reason)
            del state["pending"][key]
            continue
        try:
            quote, reason = check_trade(group, cand)
        except Exception as exc:
            mark(state, "verification_error")
            print("V33 VERIFY WARNING:", type(exc).__name__, str(exc)[:100])
            del state["pending"][key]
            continue
        if reason:
            mark(state, reason)
            del state["pending"][key]
            continue
        if topic:
            mark(state, "book_verified_" + topic)
        # A quote timestamp, not a fill, taken after book depth was checked.
        book_quoted_at = max(checked_at, int(clock())) if clock else checked_at
        # Research-only candidate baseline: price observed BEFORE risk veto.
        # It is NOT a Telegram recommendation and is NEVER a filled order.
        # Compare rejected-source picks with sent picks after resolution.
        if topic == "sports":
            shadows = state.setdefault("shadow_evidence", {})
            if dedupe not in shadows:
                shadows[dedupe] = {
                    "wallet": group["wallet"], "condition": group["condition"],
                    "token_id": cand["token"], "quoted_at": checked_at,
                    "source_trade_at": group["last"],
                    "hypothetical_copy_ask": quote["price"],
                    "hypothetical_copy_stake_usd": QUOTE_STAKE,
                    "hypothetical_copy_shares": quote.get("shares"),
                    "book_quoted_at": book_quoted_at,
                    "best_ask": quote.get("best_ask"),
                    "spread": quote.get("spread"),
                    "visible_orderbook_depth_only": True,
                    "topic": "sports", "decision": "book_verified_only",
                    "note": "SHADOW baseline, not Telegram, not filled",
                }
        try:
            recent_reason = recent_form_reason(state, group["wallet"], checked_at)
        except Exception as exc:
            mark(state, "recent_form_request_error")
            print("V33 RECENT FORM WARNING:", type(exc).__name__, str(exc)[:100])
            continue  # retry while trade is fresh; never send unchecked
        if recent_reason:
            if topic == "sports":
                state["shadow_evidence"][dedupe]["decision"] = "blocked_recent"
            mark(state, recent_reason)
            del state["pending"][key]
            continue
        if topic:
            mark(state, "recent_verified_" + topic)
        try:
            active_reason = active_form_reason(state, group["wallet"], checked_at)
        except Exception as exc:
            mark(state, "active_form_request_error")
            print("V33 ACTIVE FORM WARNING:", type(exc).__name__, str(exc)[:100])
            continue  # never send when active PnL cannot be verified
        if active_reason:
            if topic == "sports":
                state["shadow_evidence"][dedupe]["decision"] = "blocked_active"
            mark(state, active_reason)
            del state["pending"][key]
            continue
        if topic:
            mark(state, "portfolio_verified_" + topic)
        # Verification can take seconds. On 5m markets, confirm freshness
        # AGAIN just before invoking Telegram (not only at poll start).
        send_at = max(checked_at, int(clock())) if clock else checked_at
        late_reason = fast_market_reason(group, send_at)
        if not late_reason:
            try:
                late_reason = market_focus_reason(state, group, send_at)
            except Exception as exc:
                mark(state, "market_focus_request_error")
                print("V33 MARKET FOCUS WARNING:", type(exc).__name__, str(exc)[:80])
                continue
        if send_at - int(group["last"]) > MAX_AGE:
            late_reason = "expired_before_send"
        if late_reason:
            mark(state, late_reason)
            del state["pending"][key]
            continue
        try:
            recent = state.get("recent_form_cache", {}).get(group["wallet"])
            active = state.get("active_form_cache", {}).get(group["wallet"])
            receipt = v2.send_telegram(message(group, selected[group["wallet"]],
                                     cand, quote, send_at, recent=recent,
                                     active=active))
        except Exception as exc:
            mark(state, "telegram_error")
            print("V33 TELEGRAM WARNING:", type(exc).__name__, str(exc)[:100])
            continue  # unsent pending trade remains available for retry
        state["sent"][dedupe] = send_at
        if topic == "sports" and dedupe in state.get("shadow_evidence", {}):
            state["shadow_evidence"][dedupe]["decision"] = "sent_telegram"
        if isinstance(receipt, int) and receipt > 0:
            state["last_telegram_acceptance"] = {
                "at": send_at, "message_id": receipt, "kind": "buy",
                "note": "API accepted; client push/display is not verified"}
            print("V33 TELEGRAM ACCEPTED message_id=", receipt)
        mark(state, "alerts_sent")
        # Prospective paper-only evidence: the $5 executable ask was checked
        # when alerting, but no order was placed and no fill is assumed.
        # Historical alerts before this change have no trustworthy copy price.
        state.setdefault("signal_evidence", {})[dedupe] = {
            "wallet": group["wallet"], "condition": group["condition"],
            "token_id": cand["token"], "source_trade_at": group["last"],
            "alert_at": send_at, "source_price": cand["source"],
            "source_buy_usd": cand["usd"],
            "hypothetical_copy_ask": quote["price"],
            "hypothetical_copy_stake_usd": QUOTE_STAKE,
            "hypothetical_copy_shares": quote.get("shares"),
            "book_quoted_at": book_quoted_at,
            "best_ask": quote.get("best_ask"),
            "spread": quote.get("spread"),
            "visible_orderbook_depth_only": True,
            "topic": "btc" if is_btc_market(group) else "sports",
            "note": "read-only book simulation; NOT a filled order",
        }
        # Count genuine server-accepted alerts by topic from this deployment
        # onward; older historical BUY sends did not carry topic metadata.
        if MARKET_FOCUS == "sports_btc":
            mark(state, "alerts_btc" if is_btc_market(group) else "alerts_sports")
        del state["pending"][key]
        sent += 1
    return sent


def send_quiet_status(state, now, fresh_fills, watching):
    """One transparent health message/hour if no verified BUY signals."""
    history = [int(stamp) for stamp in state.get("sent", {}).values()]
    last_signal = max(history) if history else int(
        state.setdefault("monitor_started_at", now))
    last_status = int(state.get("last_quiet_status_at") or 0)
    if (now - last_signal < QUIET_STATUS_INTERVAL or
            now - last_status < QUIET_STATUS_INTERVAL):
        return False
    minutes = max(0, (now - last_signal) // 60)
    receipt = v2.send_telegram(
        "\U0001f6f0 <b>v3.3 • мониторинг работает</b>\n"
        f"\U0001f50e Проверяем {watching} кошельков.\n"
        f"\U0001f9ea Новых трейдов в последней проверке: {fresh_fills}.\n"
        f"\U0001f4e8 Последний подтверждённый BUY-сигнал: {minutes} мин назад.\n"
        "\u26a0\ufe0f Подходящих по фильтрам трейдов пока нет. "
        "Это статус, не сигнал к трейду.")
    if isinstance(receipt, int) and receipt > 0:
        state["last_telegram_acceptance"] = {
            "at": now, "message_id": receipt, "kind": "status",
            "note": "API accepted; client push/display is not verified"}
        print("V33 HEARTBEAT TELEGRAM ACCEPTED message_id=", receipt)
    state["last_quiet_status_at"] = now
    mark(state, "quiet_status_sent")
    return True


def main():
    validate_route(os.environ)
    now = int(time.time())
    source = json.loads(SHORTLIST.read_text(encoding="utf-8"))
    selected = select_wallets(source, now)
    state = load_state()
    counters_before = dict(state["counters"])
    if not state["boot_notified"]:
        v2.send_telegram(
            "\U0001f9ea <b>Tracker v3.3 подключён!</b>\n"
            f"\U0001f50e Мониторинг {len(selected)} трейдеров "
            f"из базы {source.get('candidate_count', '?')} кошельков.\n"
            "\U0001f6ab Трейды по 98–99¢ исключены; проверяются "
            "цена, книга ордеров и открытая позиция.\n"
            "Только исследования, без реальных ордеров.")
        state["boot_notified"] = True
        save_state(state)
        print("V33 START: separate Telegram bot confirmed")
    failures = baseline = fills = gaps = 0

    def poll(wallet):
        cursor = int(state["cursors"].get(wallet) or 0)
        since = now - MAX_AGE if cursor == 0 else max(
            now - MAX_CATCHUP, cursor - 120)
        return wallet, cursor, v32.get_wallet_trades(wallet, since, now)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(poll, addr): addr for addr in selected}
        for future in concurrent.futures.as_completed(jobs):
            addr = jobs[future]
            try:
                wallet, cursor, trades = future.result()
                baseline += int(cursor == 0)
                gaps += int(cursor > 0 and now - cursor > MAX_AGE)
                for row in sorted(trades, key=lambda t: (
                        int(v2.asfloat(t.get("timestamp"))), v32.trade_id(t))):
                    fills += int(add_fill(state, row, now))
                state["cursors"][wallet] = now  # only after successful read
            except Exception as exc:
                failures += 1
                print("V33 POLL WARNING:", addr[:12],
                      type(exc).__name__, str(exc)[:100])
    if failures == len(selected):
        raise RuntimeError("All wallet queries failed")
    # Conflict BUY veto remains active in deliver() even if optional
    # conflict-only Telegram chatter is muted by default.
    conflicts = (notify_opposing_wallets(state, selected, now)
                 if CONFLICT_TELEGRAM_ENABLED else 0)
    sent = deliver(state, selected, now, clock=time.time)
    if QUIET_STATUS_ENABLED and sent == 0:
        try:
            send_quiet_status(state, now, fills, len(selected))
        except Exception as exc:
            mark(state, "quiet_status_error")
            print("V33 STATUS WARNING:", type(exc).__name__, str(exc)[:100])
    # Every API catch-up is at most 2h old, so 3h of fill IDs is enough
    # to stop duplicates without growing multi-megabyte state files.
    state["seen"] = {k: v for k, v in state["seen"].items()
                     if v >= now - 3 * 3600}
    state["sent"] = {k: v for k, v in state["sent"].items()
                     if v >= now - 7 * 86400}
    state["signal_evidence"] = {
        k: v for k, v in state.get("signal_evidence", {}).items()
        if isinstance(v, dict) and
        0 <= now-int(v.get("alert_at") or 0) <= 45 * 86400}
    # Fixed bound keeps the JSON cursor checkpoint small on busy market days.
    eligible_shadow = ((k, v) for k, v in state.get("shadow_evidence", {}).items()
                       if isinstance(v, dict) and
                       0 <= now-int(v.get("quoted_at") or 0) <= 45 * 86400)
    state["shadow_evidence"] = dict(sorted(
        eligible_shadow, key=lambda item: int(item[1]["quoted_at"]),
        reverse=True)[:900])
    state["pending"] = {k: v for k, v in state["pending"].items()
                        if now - int(v["last"]) <= MAX_AGE}
    state["cursors"] = {k: v for k, v in state["cursors"].items()
                        if k in selected}
    state["recent_form_cache"] = {
        k: v for k, v in state["recent_form_cache"].items()
        if k in selected and isinstance(v, dict)
        and 0 <= now - int(v.get("checked_at") or 0) <= 3600}
    state["active_form_cache"] = {
        k: v for k, v in state["active_form_cache"].items()
        if k in selected and isinstance(v, dict)
        and 0 <= now - int(v.get("checked_at") or 0) <= 600}
    state["conflicting_markets"] = {
        k: v for k, v in state["conflicting_markets"].items()
        if 0 <= now - int(v) <= MAX_AGE}
    state["conflict_alerts"] = {
        k: v for k, v in state["conflict_alerts"].items()
        if 0 <= now - int(v) <= 86400}
    state["market_focus_cache"] = {
        k: v for k, v in state["market_focus_cache"].items()
        if isinstance(v, dict) and
        0 <= now - int(v.get("checked_at") or 0) <= MARKET_FOCUS_CACHE_SECONDS}
    state["politics_cache"] = {
        k: v for k, v in state["politics_cache"].items()
        if isinstance(v, dict) and
        0 <= now - int(v.get("checked_at") or 0) <= POLITICS_CACHE_SECONDS}
    suppression_delta = {
        k: int(n) - int(counters_before.get(k, 0))
        for k, n in state["counters"].items()
        if int(n) > int(counters_before.get(k, 0))}
    state["last_run"] = {"universe": source.get("candidate_count"),
                         "watching": len(selected), "priority_watching":
                         sum(w in selected for w in PRIORITY_WALLETS),
                         "new_fills": fills,
                         "crypto_recent_watching": sum(
                             0 <= now-int(meta.get("last_crypto_buy") or 0) <= 86400
                             for meta in selected.values()),
                         "sports_recent_watching": sum(
                             0 <= now-int(meta.get("last_sports_buy") or 0) <= 172800
                             for meta in selected.values()),
                         "initial_wallets": baseline, "alerts": sent,
                         "conflict_alerts": conflicts,
                         "request_errors": failures, "poll_gaps": gaps,
                         "this_poll": suppression_delta,
                         "suppression_totals": state["counters"]}
    save_state(state)
    print("V33 SUMMARY:", json.dumps(state["last_run"], sort_keys=True))
    print("V33 READ ONLY: never touches orders or old tracker states")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("V33 FATAL:", type(exc).__name__, str(exc)[:120])
        raise SystemExit(1)
