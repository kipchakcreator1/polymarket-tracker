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
WALLETS_PER_RUN = max(1, min(10, int(os.getenv("V33_TRUTH_WALLETS_PER_RUN", "8"))))
# Audit a bounded recent *time window*, not an arbitrary newest-1000 fills.
# Unlike the old extractor, include maker-side trades explicitly.
TRADE_PAGES = max(4, min(32, int(os.getenv("V33_TRUTH_TRADE_PAGES", "16"))))
TRADE_PAGE_SIZE = 250
TRADE_LOOKBACK_DAYS = max(30, min(365, int(os.getenv("V33_TRUTH_LOOKBACK_DAYS", "180"))))
MAX_CONDITIONS_PER_WALLET = 140
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
        bot.official_sport_market(e) for e in (market.get("events") or [])
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


# Archived sports markets are not reliably returned by Gamma /markets.
# Data API v2 /tokens is the authoritative token-id / outcome-index bridge.
SPORT_EVENT_PREFIXES = (
    "ufc", "mma", "nba", "nfl", "nhl", "mlb", "wnba", "ncaaf", "ncaab",
    "ncaa", "mls", "epl", "laliga", "serie-a", "bundesliga",
    "ligue-1", "tennis", "soccer", "f1", "formula-1", "golf"
)


def v2_token_data(condition, token, cache):
    """Match exact token AND condition to prevent false migrated-token joins."""
    token = str(token)
    key = (condition, token)
    if key not in cache:
        result = api.request_json("/v2/tokens", {"token_id": token},
                                  tries=2, timeout=12)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("Malformed v2 token response")
        rows = [row for row in result["data"] if isinstance(row, dict)
                and str(row.get("token_id") or "") == token
                and str(row.get("condition_id") or "").lower() == condition]
        cache[key] = rows[0] if len(rows) == 1 else None
    return cache[key]


def verified_sports_token(row):
    """Conservative official event-slug sports classification, not title regex."""
    if not isinstance(row, dict):
        return False
    slug = str(row.get("event_slug") or "").strip().lower()
    return any(slug == prefix or slug.startswith(prefix + "-")
               for prefix in SPORT_EVENT_PREFIXES)


def event_league(event_slug):
    slug = str(event_slug or "").strip().lower()
    for prefix in SPORT_EVENT_PREFIXES:
        if slug == prefix or slug.startswith(prefix + "-"):
            return prefix.upper()
    return "UNVERIFIED"


def v2_token_verdict(row, resolution):
    """Join authenticated V2 token's outcome_index to final 1e6 payouts."""
    if not isinstance(row, dict):
        return "unknown_token_mapping"
    if not verified_sports_token(row):
        return "not_verified_sport"
    if row.get("resolved") is not True or row.get("closed") is not True:
        return "unknown_token_unsettled"
    pays = resolved_payout(resolution)
    idx = row.get("outcome_index")
    if (pays is None or isinstance(idx, bool) or not isinstance(idx, int)
            or idx == 999 or not 0 <= idx < len(pays)):
        return "unknown_token_index"
    payout = pays[idx]
    if not (math.isclose(payout, 0, abs_tol=1)
            or math.isclose(payout, 1_000_000, abs_tol=1)):
        return "push_or_partial"
    if any(not (math.isclose(v, 0, abs_tol=1)
                or math.isclose(v, 1_000_000, abs_tol=1)) for v in pays):
        return "push_or_partial"
    final_price = row.get("final_price")
    if final_price is not None:
        try:
            quoted = float(final_price)
            if not math.isfinite(quoted) or not math.isclose(
                    quoted, payout / 1_000_000, abs_tol=0.000001):
                return "unknown_final_price_mismatch"
        except (ValueError, TypeError):
            return "unknown_final_price_mismatch"
    return "win" if math.isclose(payout, 1_000_000, abs_tol=1) else "loss"


def classify(condition, token, gamma_cache, resolution_cache):
    """Resolution correctness must survive missing/archived Gamma markets."""
    market = market_data(condition, gamma_cache)
    if market is not None and not official_sports(market):
        return "not_verified_sport", None
    resolution = resolution_data(condition, resolution_cache)
    if resolution is None:
        return "unknown_resolution_missing", None
    if str(resolution.get("status") or "").lower() != "resolved":
        return "unknown_resolution_pending", None
    if resolved_payout(resolution) is None or resolved_at(resolution) is None:
        return "unknown_resolution_incomplete", None
    if market is not None and token_index(market, token) is not None:
        return verdict(market, resolution, token), resolved_at(resolution)
    # Many historical UFC markets have verifiable V2 settlements but no
    # Gamma market lookup. Never infer outcome_index from token id text.
    row = v2_token_data(condition, token, gamma_cache)
    return v2_token_verdict(row, resolution), resolved_at(resolution)


def safe_condition(value):
    value = str(value or "").lower()
    return value if bot.v32.discovery.CONDITION_RE.fullmatch(value) else None


def audit_signals(sent, gamma_cache, resolution_cache, previous_rows=None,
                  signal_evidence=None, max_checks=MAX_SIGNAL_CONDITIONS):
    """Keep final verified evidence, refresh unresolved rows without faking PnL."""
    saved = { (x.get("wallet"), x.get("condition"), x.get("token_id")): x
              for x in (previous_rows or []) if isinstance(x, dict) }
    signal_evidence = signal_evidence or {}
    results = []
    work = 0
    check_now = int(time.time())
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
        elif (old and str(old.get("result") or "").startswith("unknown")
              and 0 <= check_now-int(old.get("checked_at") or 0) < 3600):
            # Prevent fresh pending markets from consuming the entire hourly
            # API budget. Older still-unchecked markets must make progress.
            row = old
        elif work >= max_checks:
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
                   "checked_at": check_now,
                   "settled_at": settled, "result": label}
        # A hypothetical $5 book quote, *never* the user's actual copy fill.
        # Only after final 0/1 payout; prior alerts lack entry-price evidence.
        paper = signal_evidence.get(key)
        row = dict(row)
        if (row["result"] in ("win", "loss") and isinstance(paper, dict)
                and paper.get("topic") == "sports"):
            try:
                ask = float(paper["hypothetical_copy_ask"])
                stake = float(paper["hypothetical_copy_stake_usd"])
                if (math.isfinite(ask) and math.isfinite(stake) and
                        0 < ask < 1 and 0 < stake <= 100):
                    payout = 1.0 if row["result"] == "win" else 0.0
                    row["paper_hold_pnl_no_fees"] = round(
                        stake * (payout / ask - 1), 4)
                    row["paper_stake_usd"] = stake
            except (TypeError, ValueError, KeyError):
                pass
        results.append(row)
    return results


def fetch_wallet_trades(wallet, now=None):
    """Fetch a complete time-bounded window including maker AND taker fills.

    Completeness means all pages in [now - LOOKBACK, now] were obtained. It
    does NOT mean the wallet's entire lifetime history. On cap or missing cursor,
    an incomplete flag is returned instead of inventing the omitted trades.
    """
    if not api.WALLET_RE.fullmatch(wallet):
        raise ValueError("Bad wallet address")
    now = int(now if now is not None else time.time())
    start = now - TRADE_LOOKBACK_DAYS * 86400
    cursor = None
    seen_cursors = set()
    rows = []
    for _ in range(TRADE_PAGES):
        params = {"user": wallet, "start": start, "end": now,
                  "limit": TRADE_PAGE_SIZE, "taker_only": "false"}
        if cursor:
            params["cursor"] = cursor
        value = api.request_json("/v2/trades", params, tries=2, timeout=14)
        if not (isinstance(value, dict) and isinstance(value.get("data"), list)
                and isinstance(value.get("pagination"), dict)):
            raise ValueError("Unexpected trades response")
        for row in value["data"]:
            if not isinstance(row, dict) or str(row.get("proxy_wallet", "")).lower() != wallet:
                raise ValueError("Malformed trades / wallet mismatch")
            try:
                stamp = int(row.get("timestamp"))
            except (ValueError, TypeError, OverflowError):
                raise ValueError("Trade timestamp missing")
            if not start <= stamp <= now + 60:
                raise ValueError("Trades API ignored requested time window")
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
        if side == "BUY":
            rec["first"] = min(rec["first"], stamp) if rec["first"] else stamp
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


def fetch_position_history(wallet):
    """Snapshot CLOSED plus still-held resolved positions, not just profitable exits.

    Pagination is exhaustive only up to this bounded limit. Never fill in the
    omitted tail as losses or wins, or call it the 30 latest settlements.
    """
    rows = []
    complete = True
    for status in ("CLOSED", "REDEEMABLE"):
        cursor = None
        seen = set()
        for _ in range(8):
            params = {"user": wallet, "status": status, "limit": 125,
                      "sort_by": "TIMESTAMP", "sort_direction": "DESC"}
            if status == "REDEEMABLE":
                params["include_archived"] = "true"
            if cursor:
                params["cursor"] = cursor
            result = api.request_json("/v2/positions", params, tries=2, timeout=14)
            if not (isinstance(result, dict) and
                    isinstance(result.get("data"), list) and
                    isinstance(result.get("pagination"), dict)):
                raise ValueError("Incomplete position history")
            for value in result["data"]:
                if (not isinstance(value, dict) or
                        str(value.get("proxy_wallet") or "").lower() != wallet):
                    raise ValueError("Position wallet mismatch")
                if str(value.get("status") or "").upper() != status:
                    raise ValueError("Position status mismatch")
                rows.append(value)
            page = result["pagination"]
            if page.get("has_more") is False:
                break
            nxt = page.get("next_cursor")
            if page.get("has_more") is not True or not isinstance(nxt, str) or not nxt or nxt in seen:
                raise ValueError("Invalid position history cursor")
            seen.add(nxt)
            cursor = nxt
        else:
            complete = False
    return rows, complete


def position_accounting_snapshot(rows, complete):
    """Side-by-side profitability and still-held resolved losses.

    No sports accuracy is inferred. Counts are unique (condition,token)
    positions, not independent sporting matches; last_event_at is ACTIVITY.
    """
    if not isinstance(rows, list):
        raise ValueError("Bad positions")
    data = {}
    for x in rows:
        if not isinstance(x, dict):
            raise ValueError("Malformed position")
        condition = safe_condition(x.get("condition_id"))
        token = str(x.get("token_id") or "")
        status = str(x.get("status") or "")
        if not condition or not token or status not in ("CLOSED", "REDEEMABLE"):
            continue
        realized = api.asfloat(x.get("realized_pnl"), float("nan"))
        if not math.isfinite(realized):
            raise ValueError("Missing realized PnL")
        unrealized = 0.0
        if status == "REDEEMABLE":
            if x.get("redeemable") is not True:
                raise ValueError("Redeemable position not confirmed")
            unrealized = api.asfloat(x.get("unrealized_pnl"), float("nan"))
            if not math.isfinite(unrealized):
                raise ValueError("Missing unredeemed PnL")
        net = realized + unrealized
        key = (condition, token)
        old = data.get(key)
        if old is None or net < old["net"]:
            data[key] = {"status": status, "net": net}
    closed = [x for x in data.values() if x["status"] == "CLOSED"]
    unredeemed = [x for x in data.values() if x["status"] == "REDEEMABLE"]
    return {
        "positions_complete": bool(complete),
        "closed_count": len(closed),
        "profitable_closed": sum(x["net"] > 0 for x in closed),
        "losing_closed": sum(x["net"] < 0 for x in closed),
        "profitable_closed_fraction": (round(sum(x["net"] > 0 for x in closed) /
                                            len(closed), 4) if closed else None),
        "redeemable_count": len(unredeemed),
        "unredeemed_losers": sum(x["net"] < -.01 for x in unredeemed),
        "unredeemed_major_losers_100usd": sum(
            x["net"] <= -100 for x in unredeemed),
        "unredeemed_loss_usd_observed": round(sum(
            x["net"] for x in unredeemed if x["net"] < 0), 2),
        "not_sports_winrate": True,
        "scope_note": "Mixed categories; not latest matches. Incomplete when "
                      "paginated history was truncated; cashouts can profit "
                      "without forecasting the winning match."
    }


def position_truth_sample(wallet, gamma_cache, resolution_cache, trade_snapshot=None):
    """Verified event-outcome accuracy from position history, NOT realized ROI.

    Query resolved-but-unredeemed holdings too: omitting them can systematically
    erase losers. Group by *condition*, not fills. A wallet holding opposite
    outcomes in one condition is an ambiguous directional predictor.
    """
    rows, complete = fetch_position_history(wallet)
    accounting = position_accounting_snapshot(rows, complete)
    trade_snapshot = trade_snapshot or {}
    trade_rows = trade_snapshot.get("rows")
    confirmed_trade_window = bool(trade_snapshot.get("complete")) and isinstance(trade_rows, list)
    bought = {condition: (token, first)
              for condition, token, first, cashout in candidate_positions(trade_rows or [])}
    markets = defaultdict(list)
    for pos in rows:
        condition = safe_condition(pos.get("condition_id"))
        token = str(pos.get("token_id") or "")
        if condition and token:
            markets[condition].append(pos)
    resolved = []
    status_counts = defaultdict(int)
    markets_scanned_completely = len(markets) <= 220
    for condition, entries in list(markets.items())[:220]:
        tokens = {str(v.get("token_id")) for v in entries}
        if len(tokens) != 1:
            status_counts["opposite_positions"] += 1
            continue
        pos = entries[0]
        token = next(iter(tokens))
        try:
            label, settled = classify(condition, token, gamma_cache, resolution_cache)
        except Exception:
            label, settled = "api_error", None
        status_counts[label] += 1
        if label not in ("win", "loss") or settled is None:
            continue
        trade_entry = bought.get(condition)
        if trade_rows is not None:
            # The positions endpoint does not necessarily expose first_entry_at.
            # A certified result must be backed by our own BUY fill of this
            # exact token (never by last position activity or SELL timestamp).
            if not trade_entry or trade_entry[0] != token:
                status_counts["trade_entry_unverified"] += 1
                continue
            first = trade_entry[1]
        else:
            try:
                first = int(pos.get("first_entry_at") or 0)
            except (ValueError, TypeError):
                first = 0
        if not 0 < first <= settled:
            status_counts["entry_time_unverified"] += 1
            continue
        closed = any(str(x.get("status") or "") == "CLOSED" for x in entries)
        realized = pos.get("realized_pnl")
        realized_positive = (isinstance(realized, (int, float))
                             and not isinstance(realized, bool)
                             and math.isfinite(realized) and realized > 0)
        # The earliest observed actual source BUY is a useful benchmark,
        # not the price a Telegram follower could have executed.
        first_fill = None
        if isinstance(trade_rows, list):
            buys = [r for r in trade_rows if
                    str(r.get("condition_id") or "").lower() == condition and
                    str(r.get("token_id") or "") == token and
                    r.get("side") == "BUY" and
                    int(r.get("timestamp") or 0) == first]
            if buys:
                try:
                    price = float(buys[0].get("price"))
                    if math.isfinite(price) and 0 < price < 1:
                        first_fill = price
                except (ValueError, TypeError):
                    pass
        resolved.append({
            "condition": condition, "token_id": token,
            "league": event_league(pos.get("event_slug")),
            "event_slug": str(pos.get("event_slug") or "").lower(),
            "source_first_buy_price": first_fill,
            "resolved_at": settled, "first_entry_at": first,
            "direction": label, "closed": closed,
            "profitable_closed_wrong_direction":
                bool(label == "loss" and closed and realized_positive)})
    # No positions booked in an event are treated as a missing match, NOT a loss.
    resolved.sort(key=lambda x: -x["resolved_at"])
    sampled = resolved[:MAX_WALLET_OBSERVATIONS]
    wins = sum(x["direction"] == "win" for x in sampled)
    n = len(sampled)
    league_stats = {}
    for event in sampled:
        key = event["league"]
        group = league_stats.setdefault(key, {"wins": 0, "losses": 0})
        group["wins" if event["direction"] == "win" else "losses"] += 1
    for stats in league_stats.values():
        count = stats["wins"] + stats["losses"]
        stats["verified_direction_fraction"] = round(stats["wins"] / count, 4)
    # Source buy prices are NOT copy quotes. Leave fee/slippage-sensitive
    # copied strategy ROI to our forward-only signal_evidence stream.
    return {
        "position_rows_scanned": len(rows),
        "position_accounting": accounting,
        "position_history_complete": complete,
        "position_markets_total": len(markets),
        "position_market_scan_complete": markets_scanned_completely,
        "matched_with_maker_inclusive_trades": confirmed_trade_window,
        "resolved_sports_sample": n,
        "wins": wins, "losses": n - wins,
        "by_league": league_stats,
        "observed_directional_win_fraction": round(wins / n, 4) if n else None,
        "certified_sample": bool(
            complete and markets_scanned_completely and confirmed_trade_window
            and n >= 20 and not status_counts.get("api_error")
            and not any(k.startswith("unknown") for k in status_counts)),
        "profitable_closed_wrong_direction": sum(
            x["profitable_closed_wrong_direction"] for x in sampled),
        "status_counts": dict(status_counts),
        "events": sampled,
        "caveat": "Sample sorted by final settlement, aligned with BUY token. "
                  "A certified sample requires complete time-bounded trades, "
                  "complete position history, all checked markets, and no "
                  "unknown resolution responses. Not an all-time winrate."
    }


def sample_wallet(wallet, gamma_cache, resolution_cache, trade_cache=None):
    trades, complete = fetch_wallet_trades(wallet)
    if trade_cache is not None:
        trade_cache[wallet] = {"rows": trades, "complete": complete}
    all_picks = candidate_positions(trades)
    fully_scanned = len(all_picks) <= MAX_CONDITIONS_PER_WALLET
    picks = all_picks[:MAX_CONDITIONS_PER_WALLET]
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
    # The most recent *settled* 20/30 cannot be called fully known when
    # another observed position may have a missing/broken final resolution.
    # Truly pending/unresolved events are excluded; incomplete final records
    # and any token-mapping/category ambiguity invalidate certification.
    uncertain_final = sum(count for reason, count in statuses.items()
        if reason not in ("win", "loss", "push_or_partial",
                          "unknown_resolution_pending", "not_verified_sport"))
    certified = (complete and fully_scanned and n >= 20
                 and uncertain_final == 0)
    return {"wallet": wallet, "trades_fetched": len(trades),
            "trade_history_complete": complete,
            "trade_window_days": TRADE_LOOKBACK_DAYS,
            "trade_window_complete": complete,
            "markets_in_window": len(all_picks),
            "market_scan_complete": fully_scanned,
            "unverified_final_or_mapping_count": uncertain_final,
            "includes_maker_and_taker": True,
            "markets_considered": len(picks), "status_counts": dict(statuses),
            "resolved_sports_sample": n, "wins": wins, "losses": n-wins,
            "directional_win_fraction_observed": round(wins / n, 4) if n else None,
            "certified_sample": certified,
            "history_scope": "only time-bounded recent trades; not lifetime completeness",
            "cashout_marked": sum(x["sold_before_or_after"] for x in last),
            "events": last}


def now_ts():
    return int(time.time())


def choose_wallets(shortlist, previous):
    eligible = [r["wallet"] for r in shortlist.get("shortlist", [])
                if isinstance(r, dict) and
                api.WALLET_RE.fullmatch(str(r.get("wallet") or "")) and
                int(r.get("last_sports_buy") or 0) > 0]
    if not eligible:
        return [], 0
    offset = int(previous.get("next_wallet_index") or 0) % len(eligible)
    focus = os.getenv("V33_TRUTH_FOCUS_WALLET", "").strip().lower()
    old_focus = (previous.get("wallets") or {}).get(focus) or {}
    old_events = ((old_focus.get("position_truth") or {}).get("events") or [])
    event_schema_stale = (bool(old_events) and
                          any("event_slug" not in e for e in old_events))
    focus_due = (api.WALLET_RE.fullmatch(focus) and
                 (os.getenv("V33_TRUTH_FORCE_FOCUS", "0") == "1" or
                  event_schema_stale or
                  now_ts() - int(old_focus.get("audited_at") or 0) >= 6*3600))
    selected = [focus] if focus_due else []
    from_rotation = 0
    while len(selected) < min(WALLETS_PER_RUN, len(eligible)):
        candidate = eligible[(offset + from_rotation) % len(eligible)]
        from_rotation += 1
        if candidate not in selected:
            selected.append(candidate)
    return selected, (offset + from_rotation) % len(eligible)


def aggregate(rows):
    return {"win": sum(x["result"] == "win" for x in rows),
            "loss": sum(x["result"] == "loss" for x in rows),
            "unresolved_or_uncertain": sum(str(x["result"]).startswith("unknown")
                                          for x in rows),
            "not_verified_sport": sum(x["result"] == "not_verified_sport" for x in rows),
            "push_or_partial": sum(x["result"] == "push_or_partial" for x in rows),
            "api_error": sum(x["result"] == "api_error" for x in rows),
            "not_checked": sum(x["result"] == "not_checked" for x in rows),
            "unknown_breakdown": {k: sum(x["result"] == k for x in rows)
                for k in sorted({x["result"] for x in rows
                                 if str(x["result"]).startswith("unknown")})},
            "paper_hold_sample": sum(isinstance(x.get("paper_hold_pnl_no_fees"),
                                               (int, float)) for x in rows),
            "paper_hold_pnl_no_fees": round(sum(
                x.get("paper_hold_pnl_no_fees", 0) for x in rows), 4),
            "paper_hold_total_stake": round(sum(
                x.get("paper_stake_usd", 0) for x in rows), 4)}


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
    trade_cache = {}
    # Signal state keeps short dedupe history, while prospective quote evidence
    # lives longer. Include both without double counting the same BUY.
    all_sent = dict(state.get("sent", {}))
    evidence = state.get("signal_evidence", {})
    for key, value in evidence.items():
        if isinstance(value, dict) and value.get("alert_at"):
            all_sent.setdefault(key, value["alert_at"])
    signal_rows = audit_signals(all_sent, gamma_cache, resolution_cache,
        previous.get("alerts", {}).get("rows"), evidence)
    shadow = state.get("shadow_evidence") or {}
    shadow_sent = {key: int(value.get("quoted_at"))
                   for key, value in shadow.items()
                   if isinstance(value, dict) and value.get("quoted_at")}
    shadow_rows = audit_signals(shadow_sent, gamma_cache, resolution_cache,
        previous.get("shadows", {}).get("rows"), shadow, max_checks=25)
    for row in shadow_rows:
        key = row["wallet"]+"|"+row["condition"]+"|"+row["token_id"]
        row["shadow_decision"] = (shadow.get(key) or {}).get("decision",
                                                             "not_in_state")
        row["not_a_telegram_signal"] = True
    wallets, next_index = choose_wallets(shortlist, previous)
    wallet_results = dict(previous.get("wallets") or {})
    for wallet in wallets:
        try:
            wallet_results[wallet] = sample_wallet(
                wallet, gamma_cache, resolution_cache, trade_cache)
            wallet_results[wallet]["audited_at"] = now
            try:
                wallet_results[wallet]["position_truth"] = position_truth_sample(
                    wallet, gamma_cache, resolution_cache, trade_cache.get(wallet))
            except Exception as exc:
                wallet_results[wallet]["position_truth_error"] = (
                    type(exc).__name__ + ": " + str(exc)[:90])
            print("TRUE SPORTS SAMPLE:", wallet[:12],
                  wallet_results[wallet]["resolved_sports_sample"],
                  "complete=", wallet_results[wallet]["trade_history_complete"],
                  "certified=", wallet_results[wallet]["certified_sample"])
        except Exception as exc:
            print("TRUE SPORTS ERROR:", wallet[:12], type(exc).__name__, str(exc)[:90])
            # Failed refreshes never erase earlier results or forge wins.
    report = {"schema": "v33_true_sports_audit_1", "updated_at": now,
              "method": "v2 trades + exact Gamma token mapping + final v2 payouts; "
                        "directional results; forward $5 paper HOLD only "
                        "when actual alert-time simulated ask was stored",
              "next_wallet_index": next_index,
              "alerts": {"summary": aggregate(signal_rows), "rows": signal_rows},
              "shadows": {"summary": aggregate(shadow_rows),
                          "rows": shadow_rows,
                          "note": "BOOK-VERIFIED baseline before final wallet risk "
                              "checks; NEVER a Telegram pick or real execution"},
              "wallets": wallet_results,
              "disclaimer": "20 resolved observations are preliminary. A partial "
                "trade feed is explicitly not certified. Cashouts remain distinct; "
                "past BUY prices and actual copied fills were not recorded. "
                "Forward paper HOLD assumes settlement, excludes fees and "
                "cannot stand in for the user's trading PnL."}
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False,
                                     separators=(",", ":")) + "\n")
    print("V33 TRUE SPORTS AUDIT:", json.dumps({
        "signals": report["alerts"]["summary"], "wallets_checked": len(wallets),
        "wallets_observed": len(wallet_results),
        "certified": sum(bool(x.get("certified_sample")) for x in wallet_results.values())
    }, sort_keys=True))


if __name__ == "__main__":
    main()
