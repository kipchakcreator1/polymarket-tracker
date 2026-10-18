#!/usr/bin/env python3
"""Bounded, public, read-only staged v2 trade pagination benchmark.

Never sends Telegram, updates watchlist/cursors, infers sports market type,
or treats a capped/error response as a complete trading history. Runs once
on this research workflow (not every production cycle).
"""
import concurrent.futures
import json
import math
import os
import statistics
import time
from pathlib import Path

import smart_tracker_v2 as v2

INPUT = Path("artifacts/v33-broad-sports-watch-research.json")
OUTPUT = Path("artifacts/v33-broad-sports-pagination-benchmark.json")
LIMIT = 60
RECENT_COUNT = 48
STRESS_COUNT = 12
PAGE_SIZE = 200
MAX_PAGES = 5
MAX_API_REQUESTS = LIMIT * MAX_PAGES
BATCH = 6
WORKERS = 2
WALL_SECONDS = 90
MAX_ERRORS = 3
MAX_RATE_LIMITS = 1


def select_wallets(report):
    """Choose distinct unmonitored addresses from saved sports BUY observations."""
    if not isinstance(report, dict) or report.get("schema") != (
            "v33_broad_sports_research_1"):
        raise ValueError("Unexpected sports research report")
    candidates = report.get("research_queue")
    if not isinstance(candidates, list):
        raise ValueError("Missing research queue")
    seen = set()
    pool = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        wallet = row.get("wallet")
        if (not isinstance(wallet, str) or len(wallet) != 42 or
                not wallet.startswith("0x") or
                any(ch not in "0123456789abcdef" for ch in wallet[2:]) or
                row.get("in_current_300_cursor_snapshot") is not False or
                wallet in seen):
            continue
        seen.add(wallet)
        pool.append(row)
    newest = pool[:RECENT_COUNT]
    reserved = {r["wallet"] for r in newest}
    others = [r for r in pool if r["wallet"] not in reserved]
    highest_sampled_volume = sorted(
        others,
        key=lambda r: (-max(0, int(r.get("all_buy_rows_sampled_nonunique") or 0)),
                       -max(0, int(r.get("mid_price_buy_rows_sampled_nonunique") or 0)),
                       r["wallet"]))[:STRESS_COUNT]
    return [(r["wallet"],3600,"recent_1h") for r in newest]+[
        (r["wallet"],6*3600,"sampled_busy_6h") for r in highest_sampled_volume]


def _api_error(exc):
    msg = str(exc).lower()
    if ("429" in msg or "rate limit" in msg or "too many requests" in msg):
        return "rate_limited"
    if ("timeout" in msg or isinstance(exc, TimeoutError)):
        return "timeout"
    return type(exc).__name__


def check(wallet, lookback, stage, cutoff, api=None, clock=None,
          max_pages=MAX_PAGES):
    if not 1 <= max_pages <= 12:
        raise ValueError("Unsafe research pagination budget")
    api = api or v2.request_json
    clock = clock or time.monotonic
    started = clock()
    cursor = None
    cursors = set()
    request_latencies = []
    pages = 0
    seen_rows = 0
    buy_rows = 0
    foreign_wallet_rows = 0
    error = None
    state = "UNKNOWN"
    for _ in range(max_pages):
        params = {
            "user":wallet, "start":max(1,cutoff-lookback),
            "end":cutoff,"limit":PAGE_SIZE,"taker_only":"false",
        }
        if cursor:
            params["cursor"] = cursor
        page_start = clock()
        pages += 1
        try:
            response = api("/v2/trades",params,tries=1,timeout=10)
            request_latencies.append(clock()-page_start)
            if not (isinstance(response,dict) and
                    isinstance(response.get("data"),list) and
                    isinstance(response.get("pagination"),dict) and
                    isinstance(response["pagination"].get("has_more"),bool)):
                raise ValueError("Malformed v2 trade response / pagination")
            observations=response["data"]
            if any(not isinstance(row,dict) for row in observations):
                raise ValueError("Malformed trade item")
            # Only count rows explicitly attributed to the queried wallet.
            relevant=[t for t in observations if str(t.get("proxy_wallet") or "").lower()==wallet]
            foreign_wallet_rows += len(observations)-len(relevant)
            seen_rows += len(relevant)
            buy_rows += sum(t.get("side")=="BUY" for t in relevant)
            pg=response["pagination"]
            if not pg["has_more"]:
                state="COMPLETE_QUERY_WINDOW"
                break
            next_cursor=pg.get("next_cursor")
            if (not isinstance(next_cursor,str) or not next_cursor or
                    next_cursor == cursor or next_cursor in cursors):
                state="UNKNOWN_BAD_CURSOR"
                break
            cursors.add(next_cursor)
            cursor=next_cursor
        except Exception as exc:
            error=_api_error(exc)
            state="UNKNOWN_API_OR_SCHEMA_ERROR"
            break
    else:
        state="UNKNOWN_PAGE_LIMIT"
    return {
        "wallet_prefix":wallet[:14],
        "stage":stage,
        "lookback_seconds":lookback,
        "pagination_state":state,
        "pages_requested":pages,
        "reported_buy_rows_nonunique":buy_rows,
        "reported_all_rows_nonunique":seen_rows,
        "unexpected_other_wallet_rows":foreign_wallet_rows,
        "api_error_class":error,
        "per_page_latency_seconds": [round(float(x),4) for x in request_latencies],
        "total_wall_seconds":round(clock()-started,4),
        "complete_history_beyond_query_window":False,
        "identified_sports_trades":False,
    }


def percentile(numbers,q):
    if not numbers:
        return None
    data=sorted(numbers)
    ix=max(0,min(len(data)-1,int(math.ceil(q*len(data)))-1))
    return round(data[ix],4)


def make_report(results,requested,wall_seconds,stopped,as_of):
    states={}
    errors={}
    times=[]
    for row in results:
        label=row["pagination_state"]
        states[label]=states.get(label,0)+1
        if row["api_error_class"]:
            e=row["api_error_class"]
            errors[e]=errors.get(e,0)+1
        times.extend(row["per_page_latency_seconds"])
    page_count=sum(x["pages_requested"] for x in results)
    complete=[x for x in results if x["pagination_state"]=="COMPLETE_QUERY_WINDOW"]
    multi=[x for x in results if x["pages_requested"]>1]
    return {
        "schema":"v33_60_wallet_paginated_capacity_1",
        "captured_at":as_of,
        "read_only":True,
        "stage_distribution":{
            stage:sum(x["stage"]==stage for x in results)
            for stage in ("recent_1h","sampled_busy_6h")},
        "total_planned_wallets":len(requested),
        "started_wallets":len(results),
        "not_started_due_to_protection":len(requested)-len(results),
        "stopped_early_for_safety":stopped,
        "complete_query_windows":len(complete),
        "incomplete_query_windows":len(results)-len(complete),
        "query_window_state_breakdown":states,
        "errors":errors,
        "actual_v2_page_requests":page_count,
        "max_possible_v2_page_requests":MAX_API_REQUESTS,
        "multipage_wallets_observed":len(multi),
        "complete_multipage_wallets":sum(
            x["pagination_state"]=="COMPLETE_QUERY_WINDOW" for x in multi),
        "median_page_latency_seconds":round(statistics.median(times),4) if times else None,
        "p95_page_latency_seconds":percentile(times,.95),
        "p99_page_latency_seconds":percentile(times,.99),
        "max_page_latency_seconds":round(max(times),4) if times else None,
        "overall_wall_seconds":round(wall_seconds,3),
        "total_buy_rows_in_complete_windows":sum(x["reported_buy_rows_nonunique"] for x in complete),
        "unexpected_foreign_wallet_rows":sum(x["unexpected_other_wallet_rows"] for x in results),
        "current_300_workload_not_benchmarked_here":True,
        "projected_1000_wallet_throughput_not_verified":True,
        "sports_specific_buy_confirmation_not_performed":True,
        "pagination_cursor_fail_closed":True,
        "orders_placed":0,
        "telegram_messages_sent":0,
        "production_state_updated":False,
        "research_sample_details":results,
    }


def run(report,now=None,api=None,clock=None):
    clock=clock or time.monotonic
    now=int(time.time()) if now is None else int(now)
    requested=select_wallets(report)
    first=clock()
    rows=[]
    stopped=False
    for offset in range(0,len(requested),BATCH):
        if clock()-first > WALL_SECONDS:
            stopped=True
            break
        batch=requested[offset:offset+BATCH]
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures=[pool.submit(check,w,lag,stage,now,api,clock)
                     for w,lag,stage in batch]
            chunk=[task.result() for task in futures]
        rows.extend(chunk)
        errors=sum(r["pagination_state"]=="UNKNOWN_API_OR_SCHEMA_ERROR" for r in rows)
        throttles=sum(r["api_error_class"]=="rate_limited" for r in rows)
        if errors>=MAX_ERRORS or throttles>=MAX_RATE_LIMITS:
            stopped=True
            break
    return make_report(rows,requested,clock()-first,stopped,now)


def main():
    inp=json.loads(INPUT.read_text(encoding="utf-8"))
    report=run(inp)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
    summary={k:v for k,v in report.items() if k!="research_sample_details"}
    print("V33 PAGINATED SPORTS-WALLET CAPACITY",json.dumps(summary,sort_keys=True))
    print("No Telegram, no orders, no production cursor or shortlist writes")


if __name__=="__main__":
    main()
