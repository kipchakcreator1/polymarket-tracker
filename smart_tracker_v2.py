#!/usr/bin/env python3
"""Polymarket Smart Money Tracker v2.

READ-ONLY. Uses public Polymarket Data API (legacy leaderboard/trades/closed-positions)
and Telegram Bot API. No wallet keys and no trading functionality.

Candidate population: top leaderboard users (target >=1000 distinct wallet addresses).
Scoring: recent closed-position realized PnL, not resolved-market win probability.
Alerts: new public BUYs by *qualified* wallets only.
Persists state with GitHub Actions cache between runs.
"""

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://data-api.polymarket.com"
TG = "https://api.telegram.org"
WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
STATE_VERSION = 2
CANDIDATE_TARGET = int(os.getenv("CANDIDATE_TARGET", "1250"))
SCORE_BATCH = int(os.getenv("SCORE_BATCH", "80"))
MAX_TRACKED = int(os.getenv("MAX_TRACKED", "250"))
MIN_CLOSED = int(os.getenv("MIN_CLOSED", "30"))
MIN_WINRATE = float(os.getenv("MIN_WINRATE", "0.70"))
MIN_REALIZED_PNL = float(os.getenv("MIN_REALIZED_PNL", "100"))
MIN_TRADE_USD = float(os.getenv("MIN_TRADE_USD", "1"))
SAMPLE_CLOSED = int(os.getenv("SAMPLE_CLOSED", "100"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "15"))
REFRESH_SCORES_SECONDS = 48 * 3600
REFRESH_CANDIDATES_SECONDS = 24 * 3600
HTTP_WORKERS = 5


def asfloat(x, default=0.0):
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def request_json(path, params=None, *, base=API, tries=3, timeout=22):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": "polymarket-smart-tracker-v2/1.0",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < tries:
                delay = min(8, asfloat(exc.headers.get("Retry-After"), 2 ** attempt))
                time.sleep(max(0.5, delay))
                continue
            raise RuntimeError(f"API HTTP {exc.code}: {url.split('?')[0]}") from exc
        except (OSError, TimeoutError) as exc:
            if attempt + 1 < tries:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"API network issue on {path}: {exc}") from exc
    raise RuntimeError("Request failed after retries")


def blank_state():
    return {"version": STATE_VERSION, "discovered_at": 0, "wallets": {},
            "last_scan": 0, "sent": {}, "last_report": 0}


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            obj = json.load(fp)
        if obj.get("version") != STATE_VERSION or not isinstance(obj.get("wallets"), dict):
            print("STATE: incompatible file, starting fresh")
            return blank_state()
        obj.setdefault("sent", {})
        obj.setdefault("last_scan", 0)
        return obj
    except FileNotFoundError:
        return blank_state()
    except (OSError, ValueError) as exc:
        print(f"STATE: cannot read state ({exc}), starting fresh")
        return blank_state()


def save_state(path, state):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="v2-", suffix=".json", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(state, fp, separators=(",", ":"), ensure_ascii=False)
        os.replace(temp_path, p)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def discover_candidates(state, now):
    if state["wallets"] and now - state.get("discovered_at", 0) < REFRESH_CANDIDATES_SECONDS:
        return {"updated": False, "pages": 0, "errors": 0}
    # If fewer than 1000 candidates are returned in any single leaderboard,
    # combine PnL, volume, and periods. Entries are deduplicated by wallet address.
    configs = [
        ("OVERALL", "ALL", "PNL"),
        ("OVERALL", "MONTH", "PNL"),
        ("OVERALL", "ALL", "VOL"),
        ("OVERALL", "WEEK", "PNL"),
        ("SPORTS", "ALL", "PNL"),
        ("CRYPTO", "ALL", "PNL"),
    ]
    found = dict(state["wallets"])
    pages, errors, new_users = 0, 0, 0
    for category, period, order in configs:
        for offset in range(0, 1001, 50):
            if len(found) >= CANDIDATE_TARGET:
                break
            try:
                rows = request_json("/v1/leaderboard", {
                    "category": category, "timePeriod": period,
                    "orderBy": order, "limit": 50, "offset": offset,
                })
            except RuntimeError as exc:
                errors += 1
                print(f"DISCOVERY WARNING: {category}/{period}/{order}/{offset}: {exc}")
                break
            pages += 1
            if not isinstance(rows, list):
                errors += 1
                break
            for row in rows:
                address = str(row.get("proxyWallet", "")).lower()
                if not WALLET_RE.fullmatch(address):
                    continue
                if address not in found:
                    found[address] = {"name": str(row.get("userName") or "")[:60],
                                      "lb_pnl": asfloat(row.get("pnl")),
                                      "lb_vol": asfloat(row.get("vol"))}
                    new_users += 1
                elif not found[address].get("name"):
                    found[address]["name"] = str(row.get("userName") or "")[:60]
            if len(rows) < 50:
                break
        if len(found) >= CANDIDATE_TARGET:
            break
    if found:
        state["wallets"] = found
        if pages and not errors:
            state["discovered_at"] = now
        elif pages:
            # When partial API errors happen, retry discovery sooner.
            state["discovered_at"] = now - REFRESH_CANDIDATES_SECONDS + 3600
    print(f"DISCOVERY: candidates={len(state['wallets'])} new={new_users} pages={pages} errors={errors}")
    return {"updated": True, "pages": pages, "errors": errors}


def closed_stats(wallet):
    """Recent <= SAMPLE_CLOSED closed positions by timestamp (not entire history)."""
    out = []
    for offset in range(0, SAMPLE_CLOSED, 50):
        page_size = min(50, SAMPLE_CLOSED - offset)
        page = request_json("/closed-positions", {
            "user": wallet, "limit": page_size, "offset": offset,
            "sortBy": "TIMESTAMP", "sortDirection": "DESC",
        })
        if not isinstance(page, list):
            raise RuntimeError("/closed-positions returned non-list")
        out.extend(page)
        if len(page) < page_size:
            break
    # Position close outs with realizedPnl exactly 0 count in denominator.
    pnl = [asfloat(x.get("realizedPnl")) for x in out]
    n = len(pnl)
    wins = sum(1 for v in pnl if v > 0)
    gross_gains = sum(v for v in pnl if v > 0)
    gross_losses = -sum(v for v in pnl if v < 0)
    # This is closed-position realized PnL, NOT predictive win rate or ROI.
    # No losses => profit factor is UNKNOWN (not infinite/best).
    profit_factor = round(gross_gains / gross_losses, 4) if gross_losses > 1e-8 else None
    return {"n": n, "wins": wins, "winrate": round(wins / n, 4) if n else 0,
            "recent_realized_pnl": round(sum(pnl), 2),
            "gross_gains": round(gross_gains, 4),
            "gross_losses": round(gross_losses, 4),
            "profit_factor": profit_factor,
            "net_per_closed": round(sum(pnl) / n, 4) if n else None}


def missing_copy_metrics(meta):
    """Only refresh promising cached wallets with legacy missing profit-factor fields.

    A present value of None means 'no observed losses / PF unknown', not
    missing historical data; do not refresh that wallet indefinitely.
    """
    return (
        "profit_factor" not in meta
        and meta.get("checked_at", 0) > 0
        and meta.get("n", 0) >= MIN_CLOSED
        and meta.get("winrate", 0) >= MIN_WINRATE
        and meta.get("recent_realized_pnl", float("-inf")) >= MIN_REALIZED_PNL
    )


def wallets_due_for_scoring(wallets, now, limit=None):
    """Prioritize qualified legacy-wallet migration, then regular stale scores.

    This prevents v3.2 copy gate from silently rejecting everyone solely
    because v3.1 did not persist profit_factor. Never fabricate metrics.
    """
    limit = SCORE_BATCH if limit is None else limit
    due = (
        addr for addr, meta in wallets.items()
        if missing_copy_metrics(meta)
        or now - meta.get("checked_at", 0) >= REFRESH_SCORES_SECONDS
    )
    return sorted(
        due,
        key=lambda w: (
            0 if missing_copy_metrics(wallets[w]) else 1,
            wallets[w].get("checked_at", 0),
            -wallets[w].get("lb_pnl", 0),
        ),
    )[:limit]


def score_candidates(state, now):
    wallets = state["wallets"]
    pending = wallets_due_for_scoring(wallets, now)
    if not pending:
        print("SCORING: all current candidates already scored")
        return 0, 0
    results, errors = 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=HTTP_WORKERS) as pool:
        future_map = {pool.submit(closed_stats, wallet): wallet for wallet in pending}
        for future in concurrent.futures.as_completed(future_map):
            wallet = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                errors += 1
                # Failed wallets remain eligible next time; never mark as scored.
                print(f"SCORE WARNING: {wallet[:12]}...: {exc}")
                continue
            wallets[wallet].update(result)
            wallets[wallet]["checked_at"] = now
            results += 1
    print(f"SCORING: checked_now={results} failed={errors} total_checked="
          f"{sum(1 for w in wallets.values() if w.get('checked_at'))}/{len(wallets)}")
    return results, errors


def qualified_wallets(state, now):
    eligible = []
    for wallet, meta in state["wallets"].items():
        if now - meta.get("checked_at", 0) > REFRESH_SCORES_SECONDS:
            continue
        if (meta.get("n", 0) >= MIN_CLOSED and
                meta.get("winrate", 0) >= MIN_WINRATE and
                meta.get("recent_realized_pnl", float("-inf")) >= MIN_REALIZED_PNL):
            eligible.append((wallet, meta))
    # Favor stronger sample winrate, with completed-sample PnL as tie-breaker.
    eligible.sort(key=lambda t: (t[1]["winrate"], t[1]["n"],
                                 t[1]["recent_realized_pnl"]), reverse=True)
    return eligible[:MAX_TRACKED]


def fetch_wallet_trades(wallet, since):
    # Address-scoped API avoids relying on only 1000 global recent trades.
    # Both maker and taker records included when supported by this route.
    rows = request_json("/trades", {
        "user": wallet, "side": "BUY", "limit": 100,
        "takerOnly": "false", "start": max(0, int(since)),
    })
    if not isinstance(rows, list):
        raise RuntimeError("/trades returned non-list")
    return [t for t in rows if t.get("side") == "BUY" and
            str(t.get("proxyWallet", "")).lower() == wallet]


def stable_trade_key(t):
    fields = [str(t.get(field, "")) for field in (
        "transactionHash", "proxyWallet", "asset", "side", "timestamp", "size", "price")]
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()


def trade_link(t):
    slug = str(t.get("eventSlug") or "")
    if re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", slug):
        return "https://polymarket.com/event/" + slug
    return "https://polymarket.com"


def alert_text(t, stats):
    wallet = str(t.get("proxyWallet", ""))
    usd = asfloat(t.get("size")) * asfloat(t.get("price"))
    name = (stats.get("name") or wallet[:8] + "...")[:60]
    return ("📈 <b>New public BUY (watchlist)</b>\n"
            f"👤 {html.escape(name)}\n"
            f"🏷 {html.escape(str(t.get('title') or 'Market')[:140])}\n"
            f"🎯 {html.escape(str(t.get('outcome') or '?')[:80])}\n"
            f"💵 Trade size ~${usd:,.2f}; price {asfloat(t.get('price')):.3f}\n"
            f"📊 Profitable closed positions: {stats['winrate']:.1%} "
            f"({stats['wins']}/{stats['n']} recent)\n"
            f"📊 Recent realized PnL: ${stats['recent_realized_pnl']:,.0f}\n"
            f"🔗 <a href=\"{trade_link(t)}\">Open market</a> | "
            f"<a href=\"https://polymarket.com/profile/{wallet}\">Wallet</a>\n"
            "⚠️ Past results are not a prediction. Not a trade instruction.")


def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram secrets TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing")
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                   "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"{TG}/bot{token}/sendMessage", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected message")
    except urllib.error.HTTPError as exc:
        # Do not print the URL/token in errors.
        raise RuntimeError(f"Telegram API HTTP {exc.code}") from exc


def watch_qualified(state, qualified, now):
    if not qualified:
        print("WATCH: 0 qualified wallets; no alerts possible yet")
        return 0, 0, 0
    # New installs don't alert on all historic trades; existing state allows
    # catching up after missed GitHub scheduled runs (up to 2h).
    since = max(int(state.get("last_scan", 0)) - 120, now - 7200)
    if not state.get("last_scan"):
        since = now - 900
    trades = []
    errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=HTTP_WORKERS) as pool:
        futures = {pool.submit(fetch_wallet_trades, wallet, since): (wallet, meta)
                   for wallet, meta in qualified}
        for future in concurrent.futures.as_completed(futures):
            wallet, meta = futures[future]
            try:
                for t in future.result():
                    timestamp = int(asfloat(t.get("timestamp")))
                    amount = asfloat(t.get("size")) * asfloat(t.get("price"))
                    if timestamp >= since and amount >= MIN_TRADE_USD:
                        trades.append((timestamp, t, meta))
            except Exception as exc:
                errors += 1
                print(f"WATCH WARNING: {wallet[:12]}...: {exc}")
    # Most recent first; throttle Telegram message volume, but log backlog.
    trades.sort(key=lambda row: row[0], reverse=True)
    potential = sum(stable_trade_key(t) not in state["sent"] for _, t, _ in trades)
    sent_count = 0
    for _, t, meta in trades:
        if sent_count >= MAX_ALERTS:
            break
        key = stable_trade_key(t)
        if key in state["sent"]:
            continue
        try:
            send_telegram(alert_text(t, meta))
        except RuntimeError as exc:
            print(f"TELEGRAM SEND FAILED: {exc}")
            # Don't mark sent; retry on next run.
            errors += 1
            continue
        state["sent"][key] = now
        sent_count += 1
    print(f"WATCH: wallets_queried={len(qualified)} buys_found={len(trades)} "
          f"fresh={potential} alerts_sent={sent_count} errors={errors}")
    return sent_count, potential, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="state/smart_tracker_v2.json")
    parser.add_argument("--notify-status", action="store_true",
                        help="Send one diagnostic summary to Telegram on manual run")
    args = parser.parse_args()
    now = int(time.time())
    state = load_state(args.state)
    print(f"CONFIG: candidate_target={CANDIDATE_TARGET} score_batch={SCORE_BATCH} "
          f"winrate>={MIN_WINRATE:.0%} closed>={MIN_CLOSED} "
          f"realized_pnl>=${MIN_REALIZED_PNL:g} min_trade>=${MIN_TRADE_USD:g}")
    discover = discover_candidates(state, now)
    if not state["wallets"]:
        raise RuntimeError("No candidates loaded. Polymarket API discovery failed.")
    checked, score_errors = score_candidates(state, now)
    eligible = qualified_wallets(state, now)
    print(f"SELECTION: qualified={len(eligible)} max_monitored={MAX_TRACKED}")
    alert_count, potential, watch_errors = watch_qualified(state, eligible, now)
    total_checked = sum(1 for x in state["wallets"].values() if x.get("checked_at"))
    cutoff = now - 7 * 86400
    state["sent"] = dict(sorted(((k, v) for k, v in state["sent"].items()
                                 if int(v) >= cutoff), key=lambda x: x[1], reverse=True)[:25000])
    state["last_scan"] = now
    save_state(args.state, state)
    print(f"SUMMARY: candidates={len(state['wallets'])} scored={total_checked} "
          f"qualified={len(eligible)} monitored={len(eligible)} "
          f"alerts={alert_count} score_errors={score_errors} watch_errors={watch_errors}")
    if args.notify_status:
        summary = ("🤖 <b>Polymarket Tracker v2 — diagnostic</b>\n"
                   f"Candidates discovered: {len(state['wallets'])}\n"
                   f"Wallets scored so far: {total_checked}\n"
                   f"Qualified wallets: {len(eligible)}\n"
                   f"Fresh buys in window: {potential}\n"
                   f"Alerts sent: {alert_count}\n"
                   f"Score/watch errors: {score_errors}/{watch_errors}\n"
                   "More wallets are scored on subsequent runs.")
        send_telegram(summary)
    # Discovery failing with existing cached data is okay, but if all wallet
    # score requests failed and none is qualified, signal a real diagnostic failure.
    if checked == 0 and score_errors > 0 and not eligible:
        raise RuntimeError("All wallet scoring requests failed (see SCORE WARNING)")
    if eligible and watch_errors >= len(eligible):
        raise RuntimeError("Every wallet trade request failed (see WATCH WARNING)")
    if discover["errors"] and not discover["pages"] and total_checked == 0:
        raise RuntimeError("Leaderboard inaccessible and no scored wallets cached")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
