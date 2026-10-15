#!/usr/bin/env python3
"""Standalone v3.3 research Telegram alerts from the wallet-research shortlist.

Only reads public Polymarket data. No real orders, wallet keys, or changes to
v3.1/v3.2. Closed-position win rate is NOT forecasting accuracy.
"""
import concurrent.futures
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
MAX_WALLETS = max(1, min(250, int(os.getenv("V33_MONITOR_MAX", "200"))))
MAX_ALERTS = max(1, min(12, int(os.getenv("V33_MAX_ALERTS", "6"))))
MIN_USD = float(os.getenv("V33_MIN_TRADE_USD", "50"))
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
FAST_5M_RE = re.compile(r"\b(?:btc|eth|sol|xrp)-updown-5m-(\d+)\b", re.I)
MAX_AGE = 300
MAX_CATCHUP = 7200
# Re-evaluate recent closed-position results at alert time, not only at
# the wallet-discovery refresh (which can be seven days old).
RECENT_CLOSED_WINDOW = 20
RECENT_PAGE_SIZE = 100
RECENT_LOST_LOOKBACK_SECONDS = 24 * 3600
RECENT_MIN_MATERIAL_LOSS_USD = float(os.getenv("V33_MIN_RECENT_LOSS_USD", "5"))
RECENT_MAX_UNREDEEMED_LOSSES_24H = int(os.getenv("V33_MAX_UNREDEEMED_LOSSES_24H", "2"))
RECENT_MIN_POSITIVE_RATE = .60
RECENT_MIN_PROFIT_FACTOR = 1.25
RECENT_MAX_LOSS_STREAK = 3
MAX_FAST_TRADE_AGE = 75
FAST_MARKET_END_BUFFER = 90
RECENT_CACHE_SECONDS = 180
# Active != closed: require a fresh, complete view of unrealized red positions.
ACTIVE_CACHE_SECONDS = 60
ACTIVE_PAGE_LIMIT = 100
ACTIVE_PAGE_CAP = 5
ACTIVE_MIN_ENTRY_USD = 10.0
ACTIVE_MATERIAL_LOSS_PCT = .05
ACTIVE_MAX_RED_FRACTION = float(os.getenv("V33_ACTIVE_MAX_RED_FRACTION", "0.60"))
ACTIVE_MAX_DRAWDOWN = float(os.getenv("V33_ACTIVE_MAX_DRAWDOWN", "0.15"))
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
        if not v2.WALLET_RE.fullmatch(wallet) or any(w == wallet for w, _ in qualified):
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
                            "price_profile": meta.get("price_profile")}
    if not selected:
        raise RuntimeError("No qualified wallets")
    return selected


def blank_state():
    return {"version": VERSION, "cursors": {}, "seen": {},
            "pending": {}, "sent": {}, "counters": {},
            "recent_form_cache": {}, "active_form_cache": {}, "politics_cache": {},
            "conflicting_markets": {},
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
    if not isinstance(value.setdefault("conflicting_markets", {}), dict):
        raise ValueError("Corrupted v3.3 conflict cache")
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
        response = v2.request_json("/v2/positions", {
            "user": wallet, "status": status,
            "sort_by": "TIMESTAMP", "sort_direction": "DESC",
            "limit": RECENT_PAGE_SIZE,
        }, tries=2, timeout=12)
        if not isinstance(response, dict) or not isinstance(response.get("data"), list):
            raise ValueError("Incomplete v2 recent positions response")
        if not isinstance(response.get("pagination"), dict):
            raise ValueError("Unknown v2 recent positions pagination")
        page = response["data"]
        if any(not isinstance(item, dict) for item in page):
            raise ValueError("Malformed v2 recent position")
        # Latest 100 per status are more than enough for last-20 sampling.
        # A full first page in timestamp order is sufficient for top 20.
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
                # v2 emits REDEEMABLE row status for REDEEMABLE_LOST query.
                if item.get("status") != "REDEEMABLE":
                    raise ValueError("Expected settled-but-unredeemed loss")
                if item.get("redeemable") is not True:
                    raise ValueError("Cannot verify resolved losing position")
                # Unrealized PnL on lost unredeemed holdings is the loss,
                # not a successful closed trade; include realized exits too.
                realized = v2.asfloat(item.get("realized_pnl"), float("nan"))
                unrealized = v2.asfloat(item.get("unrealized_pnl"), float("nan"))
                pnl = realized + unrealized
                if pnl >= -0.01:
                    # The public endpoint sometimes returns redeemable
                    # winners or breakeven holdings for REDEEMABLE_LOST.
                    # They are not losses; ignore the unrelated rows rather
                    # than treating every otherwise valid wallet as broken.
                    continue
            if not math.isfinite(pnl):
                raise ValueError("Missing/nonfinite recent position PnL")
            data.append({
                "realizedPnl": pnl, "timestamp": int(timestamp),
                "condition_id": condition, "token_id": token,
                "_unredeemed_lost": status == "REDEEMABLE_LOST",
            })
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
    return {"closed": len(pnls),
            "positive_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else 0.,
            "loss_streak": streak, "net_pnl": gains - losses,
            "profit_factor": gains / losses if losses > 0 else None,
            "unredeemed_lost_24h": recent_unredeemed_losses}


def recent_form_reason(state, wallet, now):
    """Fail closed on poor or unknown recent performance; cache briefly."""
    cache = state.setdefault("recent_form_cache", {})
    value = cache.get(wallet)
    if not (isinstance(value, dict) and
            "profit_factor" in value and "net_pnl" in value and
            "unredeemed_lost_24h" in value and
            0 <= now - int(value.get("checked_at") or 0) <= RECENT_CACHE_SECONDS):
        rows = fetch_recent_results(wallet, now)
        value = recent_form(rows, now)
        value["checked_at"] = now
        cache[wallet] = value
    if value["unredeemed_lost_24h"] > RECENT_MAX_UNREDEEMED_LOSSES_24H:
        return "recent_unredeemed_loss_cluster"
    if value["closed"] < RECENT_CLOSED_WINDOW:
        return "recent_sample_too_small"
    if value["loss_streak"] > RECENT_MAX_LOSS_STREAK:
        return "recent_loss_streak"
    if value["positive_rate"] < RECENT_MIN_POSITIVE_RATE:
        return "recent_positive_rate_low"
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
    rows = []
    for _ in range(ACTIVE_PAGE_CAP):
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
            return rows
        if pagination.get("has_more") is not True:
            raise ValueError("Active positions pagination status unknown")
        new_cursor = pagination.get("next_cursor")
        if not isinstance(new_cursor, str) or not new_cursor or new_cursor == cursor:
            raise ValueError("Active positions pagination cursor invalid")
        cursor = new_cursor
    raise RuntimeError("Active positions page cap: incomplete wallet snapshot")


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
    group = state["pending"].setdefault(key, {
        "wallet": wallet, "condition": condition, "last": stamp,
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
    if active:
        active_note = (
            f"\U0001f4ca Active: {active['active_count']} позиций, "
            f"красных {active['red_count']}; "
            f"нереализованный PnL {active['unrealized_pnl']:+,.0f}$\n")
    if recent:
        recent_note = (f"\U0001f4c9 Последние {recent['closed']} учтённых позиций: "
                       f"{recent['positive_rate']:.0%} прибыльных; "
                       f"лузов подряд: {recent['loss_streak']}; "
                       f"PnL: ${recent['net_pnl']:+,.0f}; "
            f"непогашенных лузов ≥$5 за 24ч: "
            f"{recent['unredeemed_lost_24h']}\n")
    return ("\U0001f9ea <b>v3.3 • новый BUY-трейд</b>\n"
            + "\U0001f464 " + name + "\n"
            + "\U0001f3f7 " + html.escape(group["title"]) + "\n"
            + "\U0001f3af " + html.escape(cand["outcome"]) + "\n"
            + f"\U0001f4b5 Трейд: \u0024{cand['usd']:,.0f} по {cand['source']:.3f}\n"
            + f"\U0001f4ca Ask: ~{quote['price']:.3f} для гипотетических "
              f"\u0024{QUOTE_STAKE:g}; задержка {(now-group['last'])//60} мин\n"
            + f"\U0001f4c8 Прибыльные закрытые позиции: {meta['winrate']:.0%} "
              "(не точность прогнозов)\n" + recent_note + active_note + link + "\n"
            + "\u26a0\ufe0f Исследовательский сигнал, не гарантия прибыли.")


def signal_sort_key(item):
    """Prioritize the requested wallet and 5-minute crypto events if capped."""
    group = item[1]
    return (group["wallet"] not in PRIORITY_WALLETS,
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


def deliver(state, selected, now):
    sent = 0
    for key, group in sorted(list(state["pending"].items()),
                             key=signal_sort_key):
        cand, reason = candidate(group, now)
        if reason:
            mark(state, reason)
            del state["pending"][key]
            continue
        if group["wallet"] not in selected:
            mark(state, "wallet_not_selected")
            del state["pending"][key]
            continue
        dedupe = key + "|" + cand["token"]
        if dedupe in state["sent"]:
            del state["pending"][key]
            continue
        if sent >= MAX_ALERTS:
            mark(state, "alert_cap")
            continue
        if conflicting_wallet_signal(state, group, cand, now):
            mark(state, "conflicting_wallets")
            del state["pending"][key]
            continue
        timing_reason = fast_market_reason(group, now)
        if timing_reason:
            mark(state, timing_reason)
            del state["pending"][key]
            continue
        try:
            politics_reason = political_market_reason(state, group, now)
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
        try:
            recent_reason = recent_form_reason(state, group["wallet"], now)
        except Exception as exc:
            mark(state, "recent_form_request_error")
            print("V33 RECENT FORM WARNING:", type(exc).__name__, str(exc)[:100])
            continue  # retry while trade is fresh; never send unchecked
        if recent_reason:
            mark(state, recent_reason)
            del state["pending"][key]
            continue
        try:
            active_reason = active_form_reason(state, group["wallet"], now)
        except Exception as exc:
            mark(state, "active_form_request_error")
            print("V33 ACTIVE FORM WARNING:", type(exc).__name__, str(exc)[:100])
            continue  # never send when active PnL cannot be verified
        if active_reason:
            mark(state, active_reason)
            del state["pending"][key]
            continue
        try:
            recent = state.get("recent_form_cache", {}).get(group["wallet"])
            active = state.get("active_form_cache", {}).get(group["wallet"])
            v2.send_telegram(message(group, selected[group["wallet"]],
                                     cand, quote, now, recent=recent,
                                     active=active))
        except Exception as exc:
            mark(state, "telegram_error")
            print("V33 TELEGRAM WARNING:", type(exc).__name__, str(exc)[:100])
            continue  # unsent pending trade remains available for retry
        state["sent"][dedupe] = now
        mark(state, "alerts_sent")
        del state["pending"][key]
        sent += 1
    return sent


def main():
    validate_route(os.environ)
    now = int(time.time())
    source = json.loads(SHORTLIST.read_text(encoding="utf-8"))
    selected = select_wallets(source, now)
    state = load_state()
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
    sent = deliver(state, selected, now)
    state["seen"] = {k: v for k, v in state["seen"].items()
                     if v >= now - 2 * 86400}
    state["sent"] = {k: v for k, v in state["sent"].items()
                     if v >= now - 7 * 86400}
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
    state["politics_cache"] = {
        k: v for k, v in state["politics_cache"].items()
        if isinstance(v, dict) and
        0 <= now - int(v.get("checked_at") or 0) <= POLITICS_CACHE_SECONDS}
    state["last_run"] = {"universe": source.get("candidate_count"),
                         "watching": len(selected), "priority_watching":
                         sum(w in selected for w in PRIORITY_WALLETS),
                         "new_fills": fills,
                         "initial_wallets": baseline, "alerts": sent,
                         "request_errors": failures, "poll_gaps": gaps,
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
