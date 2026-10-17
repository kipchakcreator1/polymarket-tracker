#!/usr/bin/env python3
"""Free Marketlens UFC 330 L2 snapshots: exact-condition lagged ASK checks.

Original data (CC BY 4.0): https://github.com/marketlenstrade/polymarket-historical-data
Marketlens, https://marketlens.trade — curated history is licensed CC BY 4.0.

No API key, wallet key, bot token or real trading. Only exact condition matches
are eligible for wallet copy simulation. Snapshot best-ask is not depth-aware
market-order VWAP; it is a strictly bounded best-quote indication.
"""
import bisect
import csv
import datetime
import json
import math
import os
import urllib.request
from collections import defaultdict
from pathlib import Path
from tools import v33_result_audit as truth

ROOT = ("https://raw.githubusercontent.com/marketlenstrade/"
        "polymarket-historical-data/main/ufc-330-2026-08-16/")
DATA_DIR = Path("/tmp/v33-marketlens-ufc330")
OUTPUT = Path("research/v33_marketlens_ufc330.json")
LAGS = (0,60,300,600)
STALE_MS = 120000


def load_csv(filename, root=ROOT, data_dir=DATA_DIR):
    data_dir.mkdir(parents=True,exist_ok=True)
    path=data_dir / filename
    if not path.exists() or path.stat().st_size == 0:
        with urllib.request.urlopen(root+filename, timeout=45) as response:
            raw=response.read(8_000_000)
        if not raw or len(raw)>=8_000_000:
            raise ValueError("Unexpected free archive file size")
        path.write_bytes(raw)
    with path.open(encoding="utf-8",newline="") as handle:
        return list(csv.DictReader(handle))


def record_time(value):
    dt=datetime.datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if not dt.tzinfo:
        raise ValueError("Historical time has no timezone")
    return int(dt.timestamp()*1000)


def book_asof(points, timestamp_ms, maximum_age_ms=STALE_MS):
    """Only use the latest historical book <= target. NEVER nearest future."""
    if not points:
        return None
    i=bisect.bisect_right(points,(int(timestamp_ms),float("inf"),float("inf")))-1
    if i<0:
        return None
    t,bid,ask=points[i]
    if t>timestamp_ms or timestamp_ms-t>maximum_age_ms:
        return None
    if not (0 < bid <= ask < 1):
        return None
    return t,bid,ask


def quote_buy(points, timestamp_ms, outcome_index):
    """Marketlens books are YES-centric. Outcome 1 (NO) asks = 1 - YES bid."""
    if outcome_index not in (0,1) or isinstance(outcome_index,bool):
        return None
    row=book_asof(points,timestamp_ms)
    if row is None:
        return None
    stamped,bid,ask=row
    return {"as_of_ms":stamped,
            "age_ms":timestamp_ms-stamped,
            "best_ask":round(ask if outcome_index==0 else 1-bid,6),
            "best_bid":round(bid if outcome_index==0 else 1-ask,6),
            "not_depth_aware":True}


def snapshot_books(rows):
    by=defaultdict(list)
    for r in rows:
        try:
            t=int(r["t_ms"])
            bid=float(r["best_bid"])
            ask=float(r["best_ask"])
            if math.isfinite(bid) and math.isfinite(ask) and 0<bid<=ask<1:
                by[r["market_id"]].append((t,bid,ask))
        except (KeyError,TypeError,ValueError,OverflowError):
            pass
    for m in by:
        by[m].sort()
    return by


def evaluate(root=ROOT, data_dir=DATA_DIR, focus=None):
    markets=load_csv("markets.csv",root,data_dir)
    market_by_condition={r["condition_id"].lower():r for r in markets}
    if len(market_by_condition)!=len(markets):
        raise ValueError("Archive has ambiguous duplicate conditions")
    books=snapshot_books(load_csv("snapshots.csv",root,data_dir))
    wallet=focus or os.getenv("V33_TRUTH_FOCUS_WALLET",
              "0x31550df5402226b825f900a31659a05d809d268e")
    trades,complete=truth.fetch_wallet_trades(wallet)
    picks=truth.candidate_positions(trades)
    overlapping=[p for p in picks if p[0] in market_by_condition]
    gamma_cache,res_cache={},{}
    rows=[]
    for condition,token,first,sold in overlapping:
        archive=market_by_condition[condition]
        try:
            resolved=record_time(archive["resolved_at"])//1000
            official,official_ts=truth.classify(condition,token,gamma_cache,res_cache)
            tokenrow=truth.v2_token_data(condition,token,gamma_cache)
            idx=tokenrow.get("outcome_index") if isinstance(tokenrow,dict) else None
            if official not in ("win","loss") or not official_ts or abs(official_ts-resolved)>86400:
                rows.append({"condition":condition,"status":"unverified_official_settlement"})
                continue
            lags={}
            for lag in LAGS:
                when=first+lag
                if when >= official_ts:
                    lags[str(lag)]={"status":"settled_before_copy"}
                    continue
                q=quote_buy(books.get(archive["market_id"],[]), when*1000,idx)
                if q is None:
                    lags[str(lag)]={"status":"no_fresh_book"}
                else:
                    price=q["best_ask"]
                    lags[str(lag)]={"status":"best_ask_proxy",
                                     "quote":q,
                                     "hold_roi_no_fees":round(1/price-1 if official=="win" else -1,5)}
            rows.append({"condition":condition,"token_id":token,
                         "first_buy_at":first,"source_sold":sold,
                         "verified_result":official,"lags":lags})
        except Exception as e:
            rows.append({"condition":condition,"status":"error_"+type(e).__name__})
    # A sample market's quote sanity check is mechanical ONLY. Do not select
    # the side or a timestamp using its winning outcome.
    control=None
    for m in markets:
        book=books.get(m["market_id"],[])
        if not book:
            continue
        first=book[0][0]
        q0=quote_buy(book,first,0)
        q1=quote_buy(book,first,1)
        if q0 and q1:
            control={"market_id":m["market_id"],
                     "condition_id":m["condition_id"],
                     "first_book_ms":first,
                     "yes_ask":q0["best_ask"],"no_ask":q1["best_ask"],
                     "synthetic_control_not_wallet_entry":True}
            break
    return {"schema":"v33_marketlens_ufc330_1",
            "data_provider":"Marketlens, https://marketlens.trade (CC BY 4.0)",
            "free_archive_markets":len(markets),
            "free_book_markets":len(books),
            "wallet":wallet,
            "wallet_trades_180d":len(trades),
            "wallet_trade_window_complete":complete,
            "wallet_candidate_conditions":len(picks),
            "exact_condition_overlaps":len(overlapping),
            "tested_wallet_placements":len(rows),
            "control_quote":control,
            "wallet_lag_experiments":rows,
            "execution_accurate_profit":None,
            "notes":"No exact condition overlap means the free UFC330 archive "
                "CANNOT evaluate this wallet's past BUYs. Never substitute a "
                "different UFC market or an illustrative quote. Point-in-time "
                "best ASK does not prove $5 depth. Marketlens full historical "
                "archive or directly recorded live-book quotes are needed for "
                "these exact conditions."}


def main():
    report=evaluate()
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,separators=(",",":"))+"\n")
    print("MARKETLENS FREE UFC330",json.dumps(report,ensure_ascii=False))


if __name__=="__main__":
    main()
