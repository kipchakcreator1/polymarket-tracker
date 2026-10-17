#!/usr/bin/env python3
"""No-key historical lag-copytest, adapted in principle from
https://github.com/howwohmm/polymarket-paper/blob/main/copytrade.py

IMPORTANT DIFFERENCES:
- Official Data API V2 payout/token settlement comes from truth audit; NEVER
  infer settled winners from last-traded or final chart prices.
- Look-ahead guard: NEVER use a price point *after* target entry time.
- Require point freshness within 180s; source first BUY must precede settlement.
- A CLOB historical PRICE point is not the past executable best ASK or
  top-of-book SIZE. This reports reference-price sensitivity, NOT actual
  copy execution ROI. A full L2 Marketlens replay is the next fidelity tier.
- No private keys, Telegram, or live orders.
"""
import json
import math
import os
from pathlib import Path
from smart_tracker_v2 import request_json
from tools import v33_fast_retro as retro

SOURCE = Path("research/v33_truth_report.json")
OUTPUT = Path("research/v33_external_copytest.json")
CLOB_URL = "https://clob.polymarket.com"
FOCUS = "0x31550df5402226b825f900a31659a05d809d268e"
LAGS_SEC = (0, 60, 300, 600)
MAX_STALE_SECONDS = 180
MAX_EVENTS = min(60, max(1, int(os.getenv("V33_EXTERNAL_COPYTEST_MAX_EVENTS", "30"))))


def clean_history(data, first, last):
    if not isinstance(data, dict) or not isinstance(data.get("history"), list):
        raise ValueError("Invalid CLOB historical response")
    out = {}
    for r in data["history"]:
        if not isinstance(r, dict):
            continue
        try:
            stamp = int(r["t"])
            price = float(r["p"])
            if isinstance(r["p"], bool) or not math.isfinite(price):
                continue
            if first <= stamp <= last and 0 < price < 1:
                if stamp in out and not math.isclose(out[stamp], price, abs_tol=1e-6):
                    raise ValueError("Conflicting historical prices same timestamp")
                out[stamp] = price
        except (ValueError, TypeError, KeyError, OverflowError) as e:
            if isinstance(e, ValueError) and "Conflicting" in str(e):
                raise
            continue
    return sorted(out.items())


def last_known_asof(points, at, *, max_stale=MAX_STALE_SECONDS):
    """No look-ahead: sampled mid/last-traded price on/before target only."""
    valid = [(ts,p) for ts,p in points if ts <= at and 0 <= at-ts <= max_stale]
    if not valid:
        return None
    return valid[-1]


def payout_hold_roi(direction, price):
    if direction not in ("win","loss") or not math.isfinite(price) or not 0 < price < 1:
        return None
    return ((1 / price) - 1) if direction == "win" else -1.0


def historical_prices(token, first_entry):
    # Bounded explicit window; interval=max can silently return empty for
    # old markets. Polymarket CLOB public endpoint requires no auth.
    response = request_json("/prices-history", {
        "market": str(token),
        "startTs": max(0, int(first_entry)-240),
        "endTs": int(first_entry)+max(LAGS_SEC)+120,
        "fidelity": 1,
    }, base=CLOB_URL, tries=2, timeout=14)
    return clean_history(response, int(first_entry)-240,
                         int(first_entry)+max(LAGS_SEC)+120)


def select_events(source, wallet):
    d=(source.get("wallets") or {}).get(wallet) or {}
    events, ambiguities=retro.unique_sports_events(
        (d.get("position_truth") or {}).get("events") or [])
    events=[x for x in events if x.get("source_first_buy_price") is not None
            and x.get("token_id") and x.get("first_entry_at")
            and x.get("resolved_at") and
            int(x["first_entry_at"]) < int(x["resolved_at"])]
    return events[:MAX_EVENTS],d,ambiguities


def evaluate(source, wallet, fetcher=historical_prices):
    events,meta,ambiguities=select_events(source,wallet)
    outputs=[]
    failures={}
    for e in events:
        entry=int(e["first_entry_at"])
        try:
            points=fetcher(str(e["token_id"]),entry)
        except Exception as exc:
            label="history_api_"+type(exc).__name__
            failures[label]=failures.get(label,0)+1
            points=[]
        results={}
        for lag in LAGS_SEC:
            at=entry+lag
            if at >= int(e["resolved_at"]):
                results[str(lag)]={"status":"market_already_settled"}
                continue
            q=last_known_asof(points, at)
            if q is None:
                results[str(lag)]={"status":"no_fresh_preresolution_price"}
                continue
            stamp, price=q
            # These are historical price observations, not executable asks.
            results[str(lag)]={"status":"price_proxy_only","price_time":stamp,
                    "price_age_seconds":at-stamp,
                    "sampled_price":round(price,6),
                    "roi_on_proxy_no_fees":round(payout_hold_roi(e["direction"],price),6),
                    "roi_if_2pct_higher_entry_no_fees":round(
                        payout_hold_roi(e["direction"],min(.9999,price*1.02)),6),
                    "roi_if_5pct_higher_entry_no_fees":round(
                        payout_hold_roi(e["direction"],min(.9999,price*1.05)),6)}
        outputs.append({
            "condition":e["condition"],"token_id":e["token_id"],
            "league":e["league"],"event_slug":e.get("event_slug"),
            "direction_confirmed_by_v2":e["direction"],
            "first_source_buy_at":entry,
            "source_first_buy_price":e["source_first_buy_price"],
            "resolved_at":e["resolved_at"],"price_points":len(points),
            "lags_seconds":results,
        })
    aggregate={}
    for lag in LAGS_SEC:
        usable=[x for x in outputs if x["lags_seconds"][str(lag)]["status"]==
                "price_proxy_only"]
        roi=[x["lags_seconds"][str(lag)]["roi_on_proxy_no_fees"] for x in usable]
        fee2=[x["lags_seconds"][str(lag)]["roi_if_2pct_higher_entry_no_fees"]
              for x in usable]
        fee5=[x["lags_seconds"][str(lag)]["roi_if_5pct_higher_entry_no_fees"]
              for x in usable]
        aggregate[str(lag)]={
            "events_with_fresh_historical_price":len(usable),
            "coverage_fraction_of_examined":round(len(usable)/len(outputs),4)
                                          if outputs else None,
            "mean_hold_roi_at_price_proxy_no_fees":round(sum(roi)/len(roi),4)
                                                    if roi else None,
            "mean_hold_roi_at_2pct_worse_entry_no_fees":round(sum(fee2)/len(fee2),4)
                                                    if fee2 else None,
            "mean_hold_roi_at_5pct_worse_entry_no_fees":round(sum(fee5)/len(fee5),4)
                                                    if fee5 else None,
        }
    # Same outcome cohort across 0/1/5/10m! Never compare means whose
    # market coverage is different; otherwise apparent edge from lag is biased.
    common=[e for e in outputs if all(e["lags_seconds"][str(l)]["status"]==
                                      "price_proxy_only" for l in LAGS_SEC)]
    common_roi={}
    for lag in LAGS_SEC:
        vals=[e["lags_seconds"][str(lag)]["roi_on_proxy_no_fees"] for e in common]
        common_roi[str(lag)]=round(sum(vals)/len(vals),4) if vals else None
    return {
        "schema":"v33_external_copytest_1","wallet":wallet,
        "source_truth_report_at":source.get("updated_at"),
        "events_examined":len(outputs),
        "source_observed_events":(meta.get("position_truth") or {}).get(
            "resolved_sports_sample",0),
        "source_outcome_sample_certified":bool(meta.get("certified_sample") and
                          (meta.get("position_truth") or {}).get("certified_sample")),
        "unverified_final_or_mapping_count":meta.get("unverified_final_or_mapping_count"),
        "ambiguous_events":ambiguities,
        "lagged_reference_price_summary":aggregate,
        "common_cohort_usable_at_all_lags":len(common),
        "common_cohort_mean_roi_at_each_lag_no_fees":common_roi,
        "history_api_errors":failures,"events":outputs,
        "actual_copy_fills":0,"execution_accurate_roi":None,
        "no_key_cheap_proxy_only":True,
        "notes":"Adapted idea of lag-adjusted copytest from howwohmm/polymarket-paper, "
            "but final winners are official V2 token payouts; no future price "
            "observations are used. Price history is sampled midpoint/last, "
            "not historical executable CLOB ask or liquidity. All numbers are "
            "unfilled price-proxy sensitivity, NO real or guaranteed profit. "
            "For genuine execution use Marketlens point-in-time historical L2 "
            "for exactly matching conditions and correct token index."
    }


def main():
    source=json.loads(SOURCE.read_text())
    result=evaluate(source,os.getenv("V33_EXTERNAL_COPYTEST_WALLET",FOCUS))
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,separators=(",",":"))+"\n")
    print("EXTERNAL COPYTEST",json.dumps({
        "wallet":result["wallet"],"events":result["events_examined"],
        "API_errors":result["history_api_errors"],
        "price_by_lag":result["lagged_reference_price_summary"],
        "matched_all_lags":result["common_cohort_usable_at_all_lags"],
        "actual_copy_fills":result["actual_copy_fills"]},sort_keys=True))


if __name__=="__main__":
    main()
