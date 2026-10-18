#!/usr/bin/env python3
"""Tiny PUBLIC read-only API capacity probe for previously unmonitored sports wallets.

24 one-page v2 trade lookups max, two workers, no persistence/cursors.
Do not infer full activity from a single page; do not send Telegram.
"""
import concurrent.futures
import json
import math
import os
import statistics
import time
from pathlib import Path
import smart_tracker_v2 as v2

INPUT=Path("artifacts/v33-broad-sports-watch-research.json")
OUTPUT=Path("artifacts/v33-broad-sports-capacity-probe.json")
MAX_WALLETS=24
WORKERS=2

def choose(report, count=MAX_WALLETS):
    if not isinstance(report,dict) or report.get("schema")!="v33_broad_sports_research_1":
        raise ValueError("Unknown broad research report")
    rows=report.get("research_queue")
    if not isinstance(rows,list) or not 1<=count<=MAX_WALLETS:
        raise ValueError("Invalid probe sample")
    return [x["wallet"] for x in rows if isinstance(x,dict) and
            not x.get("in_current_300_cursor_snapshot")][:count]

def sample(wallet,now,api=None):
    """One incomplete page is a COVERAGE WARNING, not a clean empty wallet."""
    api=api or v2.request_json
    started=time.monotonic()
    try:
        r=api("/v2/trades",{
            "user":wallet,"start":max(1,now-3600),
            "end":now,"limit":200,"taker_only":"false"
        },tries=1,timeout=10)
        elapsed=time.monotonic()-started
        if not (isinstance(r,dict) and isinstance(r.get("data"),list)
                and isinstance(r.get("pagination"),dict)
                and isinstance(r["pagination"].get("has_more"),bool)):
            raise ValueError("Unexpected public v2 response/pagination")
        rows=r["data"]
        if any(not isinstance(t,dict) for t in rows):
            raise ValueError("Malformed public trade row")
        relevant=[t for t in rows if str(t.get("proxy_wallet") or "").lower()==wallet]
        return {"wallet":wallet,"ok":True,"latency_seconds":round(elapsed,2),
                "rows_in_first_page":len(relevant),
                "buy_rows_in_first_page":sum(t.get("side")=="BUY" for t in relevant),
                "has_more":r["pagination"]["has_more"],
                "complete_one_hour_trade_window":not r["pagination"]["has_more"]}
    except Exception as exc:
        return {"wallet":wallet,"ok":False,"error":type(exc).__name__,
                "latency_seconds":round(time.monotonic()-started,2),
                "complete_one_hour_trade_window":False}

def report_results(items):
    times=sorted(float(x.get("latency_seconds") or 0) for x in items)
    errors={}
    for x in items:
        if not x.get("ok"):errors[x.get("error","Unknown")]=errors.get(x.get("error","Unknown"),0)+1
    complete=[x for x in items if x.get("complete_one_hour_trade_window")]
    return {"schema":"v33_broad_wallet_capacity_canary_1",
            "checked_wallets":len(items),
            "read_only_public_v2_first_page_requests":len(items),
            "successful_one_page_responses":sum(bool(x.get("ok")) for x in items),
            "complete_one_hour_windows":len(complete),
            "partial_pagination_not_counted_complete":sum(bool(x.get("has_more")) for x in items),
            "errors":errors,
            "p50_single_request_seconds":statistics.median(times) if times else None,
            "max_single_request_seconds":max(times) if times else None,
            "buy_rows_in_fully_retrieved_one_hour_windows":sum(x.get("buy_rows_in_first_page",0) for x in complete),
            "sports_classification_not_performed":True,
            "sampled_trade_rows_nonunique":True,
            "extrapolate_to_1000_with_caution":True,
            "request_429s_unverified_until_real_measurement":True,
            "live_wallet_watchlist_updated":False,
            "telegram_sends":0,
            "real_orders":0}

def run(report,now=None,api=None):
    now=int(time.time() if now is None else now)
    sample_wallets=choose(report)
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        items=list(pool.map(lambda w:sample(w,now,api),sample_wallets))
    return report_results(items)

def main():
    report=json.loads(INPUT.read_text())
    output=run(report)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(output,indent=2,sort_keys=True)+"\n")
    print("V33 BROAD SPORTS PUBLIC API CANARY",json.dumps(output,sort_keys=True))

if __name__=="__main__":
    main()
