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
RECENT_MIN_POSITIVE_RATE = .60
RECENT_MIN_PROFIT_FACTOR = 1.25
RECENT_MAX_LOSS_STREAK = 3
MAX_FAST_TRADE_AGE = 75
FAST_MARKET_END_BUFFER = 90
RECENT_CACHE_SECONDS = 180
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
            "recent_form_cache": {}, "boot_notified": False}


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
    return value


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False,
                               separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(STATE)


def recent_form(rows):
    """Snapshot of the newest closed positions, ordered by API timestamp DESC.

    Positive realized PnL is NOT event forecasting accuracy; zero PnL does not
    count as profitable, but also does not extend a negative-PnL losing streak.
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
    return {"closed": len(pnls),
            "positive_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else 0.,
            "loss_streak": streak, "net_pnl": gains - losses,
            "profit_factor": (gains / losses if losses > 0 else
                              float("inf") if gains > 0 else 0.)}


def recent_form_reason(state, wallet, now):
    """Fail closed on poor or unknown recent performance; cache briefly."""
    cache = state.setdefault("recent_form_cache", {})
    value = cache.get(wallet)
    if not (isinstance(value, dict) and
            "profit_factor" in value and "net_pnl" in value and
            0 <= now - int(value.get("checked_at") or 0) <= RECENT_CACHE_SECONDS):
        rows = v2.request_json("/closed-positions", {
            "user": wallet, "limit": RECENT_CLOSED_WINDOW, "offset": 0,
            "sortBy": "TIMESTAMP", "sortDirection": "DESC",
        }, tries=2, timeout=12)
        value = recent_form(rows)
        value["checked_at"] = now
        cache[wallet] = value
    if value["closed"] < RECENT_CLOSED_WINDOW:
        return "recent_sample_too_small"
    if value["loss_streak"] > RECENT_MAX_LOSS_STREAK:
        return "recent_loss_streak"
    if value["positive_rate"] < RECENT_MIN_POSITIVE_RATE:
        return "recent_positive_rate_low"
    if value["net_pnl"] <= 0 or value["profit_factor"] < RECENT_MIN_PROFIT_FACTOR:
        return "recent_negative_expectancy_sample"
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
    if quote.get("spread") is None or quote["spread"] > MAX_SPREAD:
        return None, "spread_too_wide"
    if ask - quote["best_ask"] > MAX_BOOK_SLIPPAGE:
        return None, "book_slippage"
    return quote, None


def message(group, meta, cand, quote, now, recent=None):
    name = html.escape(meta["name"] or group["wallet"][:12])
    url = html.escape(group.get("url") or "", quote=True)
    link = f'<a href="https://polymarket.com/profile/{group["wallet"]}">Кошелёк</a>'
    if url:
        link = f'<a href="{url}">Рынок</a> | ' + link
    recent_note = ""
    if recent:
        recent_note = (f"\U0001f4c9 Последние {recent['closed']} закрытий: "
                       f"{recent['positive_rate']:.0%} прибыльных; "
                       f"лузов подряд: {recent['loss_streak']}; "
                       f"PnL: ${recent['net_pnl']:+,.0f}\n")
    return ("\U0001f9ea <b>v3.3 • новый BUY-трейд</b>\n"
            + "\U0001f464 " + name + "\n"
            + "\U0001f3f7 " + html.escape(group["title"]) + "\n"
            + "\U0001f3af " + html.escape(cand["outcome"]) + "\n"
            + f"\U0001f4b5 Трейд: \u0024{cand['usd']:,.0f} по {cand['source']:.3f}\n"
            + f"\U0001f4ca Ask: ~{quote['price']:.3f} для гипотетических "
              f"\u0024{QUOTE_STAKE:g}; задержка {(now-group['last'])//60} мин\n"
            + f"\U0001f4c8 Прибыльные закрытые позиции: {meta['winrate']:.0%} "
              "(не точность прогнозов)\n" + recent_note + link + "\n"
            + "\u26a0\ufe0f Исследовательский сигнал, не гарантия прибыли.")


def signal_sort_key(item):
    """Prioritize the requested wallet and 5-minute crypto events if capped."""
    group = item[1]
    return (group["wallet"] not in PRIORITY_WALLETS,
            FAST_5M_RE.search(group.get("url") or "") is None,
            -int(group["last"]))


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
        timing_reason = fast_market_reason(group, now)
        if timing_reason:
            mark(state, timing_reason)
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
            recent = state.get("recent_form_cache", {}).get(group["wallet"])
            v2.send_telegram(message(group, selected[group["wallet"]],
                                     cand, quote, now, recent=recent))
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
