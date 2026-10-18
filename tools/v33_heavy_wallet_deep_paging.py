#!/usr/bin/env python3
"""Read-only narrow follow-up for wallets hitting the five-page stress cap.

The exact query cutoff and lookback come from a previous saved public data
benchmark. Never claim unknown-history wallets as validated or write runtime.
"""
import concurrent.futures
import collections
import json
import statistics
import time
from pathlib import Path

from tools import v33_pagination_capacity_benchmark as benchmark

BROAD = Path("artifacts/v33-broad-sports-watch-research.json")
FIRST = Path("artifacts/v33-broad-sports-pagination-benchmark.json")
OUTPUT = Path("artifacts/v33-broad-sports-deep-paging.json")
MAX_WALLETS = 6
MAX_PAGES = 12
WORKERS = 2


def choose_missing(first, broad):
    if not isinstance(first, dict) or first.get("schema") != (
            "v33_60_wallet_paginated_capacity_1"):
        raise ValueError("Unrecognized five-page benchmark")
    if not isinstance(broad, dict) or broad.get("schema") != (
            "v33_broad_sports_research_1"):
        raise ValueError("Unrecognized research universe")
    lookup = collections.defaultdict(set)
    for row in broad.get("research_queue") or []:
        if not isinstance(row,dict) or row.get("in_current_300_cursor_snapshot") is not False:
            continue
        wallet=row.get("wallet")
        if isinstance(wallet,str) and len(wallet)==42:
            lookup[wallet[:14]].add(wallet)
    selected=[]
    unknown=[]
    seen=set()
    for item in first.get("research_sample_details") or []:
        if not isinstance(item,dict) or item.get("pagination_state")!="UNKNOWN_PAGE_LIMIT":
            continue
        prefix=item.get("wallet_prefix")
        if prefix in seen:
            continue
        seen.add(prefix)
        possible=lookup.get(prefix) or set()
        if len(possible)!=1:
            unknown.append({"wallet_prefix":prefix,"reason":"wallet_prefix_not_uniquely_resolved"})
            continue
        lag=item.get("lookback_seconds")
        if lag not in (3600,21600):
            unknown.append({"wallet_prefix":prefix,"reason":"unsupported_window"})
            continue
        selected.append((next(iter(possible)), lag,item.get("stage")))
    return selected[:MAX_WALLETS], unknown, max(0,len(selected)-MAX_WALLETS)


def run(first,broad,api=None,clock=None):
    clock=clock or time.monotonic
    chosen,unknown,overflow=choose_missing(first,broad)
    stamp=first.get("captured_at")
    if not isinstance(stamp,int) or stamp<1:
        raise ValueError("Original query cutoff unavailable")
    start=clock()
    results=[]
    rate_errors=0
    for offset in range(0,len(chosen),WORKERS):
        if rate_errors:
            break
        batch=chosen[offset:offset+WORKERS]
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures=[
                executor.submit(benchmark.check,w,lookback,stage,stamp,api,clock,
                                MAX_PAGES) for w,lookback,stage in batch]
            retrieved=[f.result() for f in futures]
        results.extend(retrieved)
        rate_errors+=sum(x["api_error_class"]=="rate_limited" for x in retrieved)
    pages=sum(x["pages_requested"] for x in results)
    per_page=[v for x in results for v in x["per_page_latency_seconds"]]
    statuses=dict(collections.Counter(x["pagination_state"] for x in results))
    return {
        "schema":"v33_heavy_wallet_deep_pagination_1",
        "source_cutoff":stamp,
        "same_lookback_window_as_original":True,
        "source_first_pass_incomplete_windows":sum(
            x.get("pagination_state")=="UNKNOWN_PAGE_LIMIT"
            for x in first.get("research_sample_details") or []),
        "planned_deep_wallets":len(chosen),
        "started_deep_wallets":len(results),
        "unresolved_prefixes":unknown,
        "deep_wallets_skipped_due_to_cap":overflow,
        "stopped_after_rate_limit":bool(rate_errors),
        "complete_query_windows_after_12_page_cap":statuses.get("COMPLETE_QUERY_WINDOW",0),
        "still_incomplete_or_error":len(results)-statuses.get("COMPLETE_QUERY_WINDOW",0),
        "status_breakdown":statuses,
        "deep_total_actual_v2_pages_including_replayed_first_5":pages,
        "deep_theoretical_max_v2_pages":MAX_WALLETS*MAX_PAGES,
        "p50_page_latency_seconds":round(statistics.median(per_page),4) if per_page else None,
        "p95_page_latency_seconds":benchmark.percentile(per_page,.95),
        "wall_seconds":round(clock()-start,3),
        "observed_buy_rows_in_completed_query_windows":sum(
            x["reported_buy_rows_nonunique"] for x in results if
            x["pagination_state"]=="COMPLETE_QUERY_WINDOW"),
        "read_only":True,
        "automatically_promote_wallets":False,
        "real_time_monitor_not_benchmarked":True,
        "sports_classification_not_verified":True,
        "complete_wallet_profit_and_loss_not_verified":True,
        "telegram_messages_sent":0,
        "production_state_updated":False,
        "per_wallet_results":results,
    }


def main():
    first=json.loads(FIRST.read_text(encoding="utf-8"))
    broad=json.loads(BROAD.read_text(encoding="utf-8"))
    out=run(first,broad)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("V33 HEAVY WALLET DEEP PAGE FOLLOWUP",
          json.dumps({k:v for k,v in out.items() if k!="per_wallet_results"},sort_keys=True))


if __name__=="__main__":
    main()
