#!/usr/bin/env python3
"""Independent multi-wallet SPORT historical copy PRICE-PROXY audit.

Reuses howwohmm/polymarket-paper's lag approach through v33_external_copytest,
but final outcomes always come from official Polymarket v2 resolved-token
joins, not last sampled prices. Strictly research: no real fills, no Telegram,
no private keys. Mean PnL is selected-history proxy, NOT certified copy ROI.
"""
import concurrent.futures
import json
import os
from pathlib import Path
from tools import v33_external_copytest as paper
from tools import v33_fast_retro as retro

SOURCE=Path("research/v33_truth_report.json")
OUTPUT=Path("research/v33_wallet_copy_cohort.json")
MAX_WALLETS=max(1,min(20,int(os.getenv("V33_COHORT_MAX_WALLETS","12"))))
MIN_EVENTS=max(3,int(os.getenv("V33_COHORT_MIN_EVENTS","8")))
WORKERS=max(1,min(6,int(os.getenv("V33_COHORT_WORKERS","4"))))
FOCUS="0x31550df5402226b825f900a31659a05d809d268e"
NEGATIVE_CONTROL="0x906aff5eb0c700a1fa2dee7c1b766c3384a4714e"


def qualified_event_list(row):
    events,ambiguities=retro.unique_sports_events(
        (row.get("position_truth") or {}).get("events") or [])
    return [e for e in events if
            e.get("token_id") and e.get("first_entry_at") and
            e.get("resolved_at") and
            e.get("source_first_buy_price") is not None and
            int(e["first_entry_at"])<int(e["resolved_at"])],ambiguities


def choose_wallets(source,max_wallets=MAX_WALLETS):
    wallets=source.get("wallets") or {}
    ranked=[]
    for addr,data in wallets.items():
        if not isinstance(data,dict):
            continue
        if (data.get("position_truth") or {}).get("position_scan_selection") != (
                "recent_original_BUY_across_CLOSED_and_REDEEMABLE"):
            # Old CLOSED-first history has survivor-biased reported wins.
            # Do not even request sampled prices for such observations.
            continue
        es,amb=qualified_event_list(data)
        if len(es)<MIN_EVENTS:
            continue
        ranked.append((addr,len(es),
                       bool(data.get("trade_window_complete")),
                       bool((data.get("position_truth") or {}).get("position_history_complete"))))
    # Make comparators explicit: failing UFC case plus less-positive other
    # wallet to catch "all winners" survivor-only selection.
    ranked.sort(key=lambda x:(-int(x[2] and x[3]),-x[1],x[0]))
    chosen=[]
    for special in (FOCUS,NEGATIVE_CONTROL):
        if any(x[0]==special for x in ranked) and special not in chosen:
            chosen.append(special)
    for item in ranked:
        if len(chosen)>=max_wallets:
            break
        if item[0] not in chosen:
            chosen.append(item[0])
    return chosen[:max_wallets],len(ranked)


def _round(x):
    return round(x,5) if x is not None else None


def screen_one(result, data):
    events=result.get("events") or []
    common=[e for e in events if all(
        (e.get("lags_seconds") or {}).get(str(lag),{}).get("status")
        =="price_proxy_only" for lag in paper.LAGS_SEC)]
    common_n=len(common)
    selected=len(events)
    unknown=int(data.get("unverified_final_or_mapping_count") or 0)
    benchmark=[]
    baseline=[]
    by_league={}
    for event in common:
        point=event["lags_seconds"]["300"]
        benchmark.append(float(point["roi_if_5pct_higher_entry_no_fees"]))
        baseline.append(float(point["roi_on_proxy_no_fees"]))
        league=event["league"]
        by_league.setdefault(league,[]).append(
            float(point["roi_if_5pct_higher_entry_no_fees"]))
    observed=_round(sum(benchmark)/common_n) if common_n else None
    reference=_round(sum(baseline)/common_n) if common_n else None
    # Highly conservative *scenario*, NOT a quantitative estimate:
    # Missing price observations and additional unverifiable settled markets
    # treated as losses. Unverified records are market IDs rather than
    # necessarily distinct sporting events; scenario is for caution only.
    missing=max(0,selected-common_n)
    total=selected+unknown
    stress=_round((sum(benchmark)-missing-unknown)/total) if total else None
    coverage=_round(common_n/selected) if selected else None
    blockers=[]
    if not data.get("trade_window_complete"):
        blockers.append("incomplete_trade_window")
    if not data.get("market_scan_complete"):
        blockers.append("truncated_sports_markets")
    pos=data.get("position_truth") or {}
    scan_revision_ok=(pos.get("position_scan_selection")==
        "recent_original_BUY_across_CLOSED_and_REDEEMABLE")
    if not scan_revision_ok:
        blockers.append("old_CLOSED_first_scan_can_hide_REDEEMABLE_losses")
    if not pos.get("position_history_complete"):
        blockers.append("incomplete_position_history")
    if not pos.get("position_market_scan_complete"):
        blockers.append("truncated_position_history")
    if unknown:
        blockers.append("unverified_final_outcomes")
    if not data.get("certified_sample") or not pos.get("certified_sample"):
        blockers.append("uncertified_outcome_sample")
    large=(pos.get("position_accounting") or {}).get(
        "unredeemed_major_losers_100usd")
    if isinstance(large,int) and large>=4:
        blockers.append("multiple_major_unredeemed_losses")
    if selected < MIN_EVENTS:
        blockers.append("insufficient_verified_event_count")
    if common_n < MIN_EVENTS:
        blockers.append("insufficient_price_coverage")
    if coverage is None or coverage<.8:
        blockers.append("missing_price_coverage")
    if result.get("history_api_errors"):
        blockers.append("history_api_errors")
    status=("INVALID_SOURCE_SCAN_ORDER" if not scan_revision_ok else
            "OBSERVED_POSITIVE_PRICE_PROXY" if
            common_n>=MIN_EVENTS and observed is not None and observed>0 and
            coverage>=.8 else
            "OBSERVED_NEGATIVE_PRICE_PROXY" if
            common_n>=MIN_EVENTS and observed is not None and observed<=0 and
            coverage>=.8 else "INSUFFICIENT_PRICE_HISTORY")
    return {"wallet":result.get("wallet"),
            "status":status,
            "historical_first_buy_sample":selected,
            "common_cohort_all_four_lags":common_n,
            "common_price_coverage":coverage,
            "unknown_final_or_mapping_markets":unknown,
            "source_trade_window_complete":bool(data.get("trade_window_complete")),
            "observed_price_proxy_hold_roi_5m":reference,
            "roi_if_5pct_worse_entry_5m_no_fees":observed,
            "stress_if_all_missing_quotes_and_unverified_markets_lose":stress,
            "stress_not_actual_expected_roi":True,
            "by_league_5pct_worse_entry":{
                lg:{"observations":len(vals),
                    "mean_proxy_hold_roi_no_fees":_round(sum(vals)/len(vals))}
                for lg,vals in by_league.items()},
            "historical_data_warnings":blockers,
            "actual_copy_trades":0,"copy_roi_verified":False,
            "eligible_for_live_buy":False,
            "lag_comparison_common_cohort":
                result.get("common_cohort_mean_roi_at_each_lag_no_fees"),
            "api_failures":result.get("history_api_errors") or {},
            "no_lookahead_proxy_only":True}


def compile_report(source, results, selected, eligible):
    wallets=source.get("wallets") or {}
    rows=[]
    for address in selected:
        if address not in results:
            rows.append({"wallet":address,"status":"FETCH_ERROR",
                         "eligible_for_live_buy":False,
                         "copy_roi_verified":False})
        else:
            rows.append(screen_one(results[address],wallets[address]))
    statuses={"INVALID_SOURCE_SCAN_ORDER":0,
              "OBSERVED_POSITIVE_PRICE_PROXY":0,
              "OBSERVED_NEGATIVE_PRICE_PROXY":0,
              "INSUFFICIENT_PRICE_HISTORY":0,"FETCH_ERROR":0}
    for r in rows:
        statuses[r["status"]]=statuses.get(r["status"],0)+1
    # Order by cohort coverage/reliability, NOT by observed return.
    rows.sort(key=lambda r:(
        r.get("status")=="FETCH_ERROR",
        -r.get("common_cohort_all_four_lags",0),
        r.get("wallet","")))
    return {"schema":"v33_wallet_copy_cohort_1",
            "input_truth_updated_at":source.get("updated_at"),
            "audited_wallet_population":len(wallets),
            "wallets_with_minimum_purchasable_historical_events":eligible,
            "wallets_tested":len(selected),
            "unrepaired_prior_wallet_studies_excluded":sum(
                isinstance(row,dict) and
                (row.get("position_truth") or {}).get("position_scan_selection") !=
                "recent_original_BUY_across_CLOSED_and_REDEEMABLE"
                for row in wallets.values()),
            "max_events_per_wallet":paper.MAX_EVENTS,
            "lag_seconds":list(paper.LAGS_SEC),
            "requires_unbiased_mixed_status_scan":True,
            "failure_stress":"unpriced selected events + unresolved markets set "
                             "to -100% as illustration, NOT evidence of actual ROI",
            "status_counts":statuses,
            "wallets":rows,
            "live_approved":0,
            "disclaimer":"All historical results refer to sampled CLOB price lines, "
                         "NOT historical executable asks, order-book depth, fill size, "
                         "fees or forward out-of-sample copy returns. The selected "
                         "observed resolved outcomes can suffer survivorship bias. "
                         "No wallet is approved for BUY by this report."}


def main():
    source=json.loads(SOURCE.read_text())
    selected,eligible=choose_wallets(source)
    results={}
    errors={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs={pool.submit(paper.evaluate,source,w):w for w in selected}
        for future in concurrent.futures.as_completed(jobs):
            wallet=jobs[future]
            try:
                results[wallet]=future.result()
            except Exception as error:
                errors[wallet]=str(error)[:180]
    report=compile_report(source,results,selected,eligible)
    report["errors"]=errors
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,
                                 separators=(",",":"))+"\n")
    print("V33 WALLET COPY COHORT",json.dumps({
        "wallets_tested":len(selected),"eligible_for_research":eligible,
        "statuses":report["status_counts"],
        "live_approved":report["live_approved"],
        "errors":errors},sort_keys=True))
    for r in report["wallets"]:
        print("COPY PRICE PROXY",json.dumps({
            k:r.get(k) for k in ("wallet","status",
                "common_cohort_all_four_lags",
                "unknown_final_or_mapping_markets",
                "roi_if_5pct_worse_entry_5m_no_fees",
                "stress_if_all_missing_quotes_and_unverified_markets_lose",
                "eligible_for_live_buy")},sort_keys=True))


if __name__=="__main__":
    main()
