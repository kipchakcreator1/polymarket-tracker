#!/usr/bin/env python3
"""Observational risk-veto outcome inventory from a durable sports quote archive.

Read-only; no Telegram, orders, live threshold changes, or backfilled asks.
All ROI is hypothetical HOLD on a verified saved quote. This is not an
independent-sports-game randomized or correctly matched filter evaluation.
"""
import collections
import json
import math
from pathlib import Path

from tools.v33_forward_net_paper import paper_pnl
from tools.v33_shadow_checkpoint import SCHEMA, key_parts

INPUT=Path("research/v33-shadow-outcome-checkpoint.json")
OUTPUT=Path("artifacts/v33-risk-reason-settlement.json")
PROVENANCE="same_original_live_quote_at_archive_capture"
DECISION_TO_GATE={"blocked_recent":"recent",
                  "blocked_active":"active_portfolio",
                  "sent_telegram":"passed"}
SCENARIOS={"assumed_5pct_fee":(.05,1),
           "assumed_5pct_fee_plus_5pct_worse_ask":(.05,1.05)}


def cohort(row):
    """Keep all old or unproven risk explanations UNKNOWN, never impute."""
    d=row.get("shadow_decision")
    if (row.get("risk_provenance")!=PROVENANCE or
            row.get("risk_gate")!=DECISION_TO_GATE.get(d)):
        return "legacy_or_unproven_reason"
    r=row.get("risk_reason")
    if d=="sent_telegram":
        if r is not None:
            return "legacy_or_unproven_reason"
        return "passed:telegram_sent"
    if not isinstance(r,str) or not 1<=len(r)<=128:
        return "legacy_or_unproven_reason"
    return str(row["risk_gate"])+":"+r


def quote_model_eligible(row):
    """A model candidate is still NOT a known achievable client fill."""
    if row.get("result") not in ("win","loss"):
        return None,"not_final"
    proof=row.get("original_book_quote")
    if not isinstance(proof,dict) or proof.get("no_fill_assumed") is not True:
        return None,"missing_original_quote"
    if proof.get("topic")!="sports":
        return None,"non_sports_quote"
    try:
        ask=float(proof["hypothetical_copy_ask"])
        stake=float(proof["hypothetical_copy_stake_usd"])
        shares=float(proof["hypothetical_copy_shares"])
        at=int(proof["book_quoted_at"])
        traded=int(proof["source_trade_at"])
        settled=int(row["settled_at"])
    except (KeyError,ValueError,TypeError,OverflowError):
        return None,"malformed_quote_or_settlement"
    if not (all(map(math.isfinite,(ask,stake,shares))) and
            0<ask<1 and 0<stake<=100 and shares>0 and
            math.isclose(ask*shares,stake,rel_tol=.025,abs_tol=.025)):
        return None,"invalid_price_or_depth"
    if not (0<traded<=at<settled and at-traded<=300 and
            abs(at-int(row.get("alert_at") or 0))<=2):
        return None,"missing_proven_before_settlement_chronology"
    return {"ask":ask,"stake":stake,"win":row["result"]=="win"},None


def summarize(rows):
    n=len(rows)
    finals=sum(x.get("result") in ("win","loss") for x in rows)
    with_quote=sum(isinstance(x.get("original_book_quote"),dict) for x in rows)
    return {"observed_position_keys":n,
            "independent_sports_events_verified":False,
            "final_classifications":finals,
            "unresolved_or_unverified":n-finals,
            "has_original_book_quote":with_quote,
            "missing_original_book_quote":n-with_quote,
            "sampled_unique_wallets":len({x["wallet"] for x in rows}),
            "position_level_final_wins":sum(x.get("result")=="win" for x in rows),
            "position_level_final_losses":sum(x.get("result")=="loss" for x in rows)}


def compile_report(checkpoint):
    if (not isinstance(checkpoint,dict) or checkpoint.get("schema")!=SCHEMA
            or not isinstance(checkpoint.get("rows"),list)):
        raise ValueError("Invalid archived sports risk evidence schema")
    rows=checkpoint["rows"]
    groups=collections.defaultdict(list)
    conditions=collections.defaultdict(list)
    rejected_identity=0
    for x in rows:
        ident=key_parts(x)
        if ident is None:
            rejected_identity+=1
            continue
        y=dict(x,wallet=ident[0],condition=ident[1],token_id=ident[2])
        label=cohort(y)
        groups[label].append(y)
        conditions[y["condition"]].append(y)
    collided={k for k,r in conditions.items() if len(r)>1}
    result_groups={}
    for label,data in sorted(groups.items()):
        info=summarize(data)
        declines=collections.Counter()
        eligible=[]
        all_final=0
        for row in data:
            if row["result"] not in ("win","loss"):
                continue
            all_final+=1
            if row["condition"] in collided:
                declines["repeated_market_condition_across_all_cohorts"]+=1
                continue
            candidate,reason=quote_model_eligible(row)
            if candidate is None:
                declines[reason]+=1
                continue
            eligible.append(candidate)
        scenarios={}
        for name,(fee,multiplier) in SCENARIOS.items():
            simulations=[
                (x["stake"],paper_pnl(x["stake"],x["ask"],x["win"],
                                      fee,multiplier))
                for x in eligible
            ]
            sims=[(stake,pnl) for stake,pnl in simulations if pnl is not None]
            denominator=sum(stake for stake,pnl in sims)
            scenarios[name]={
                "priced_unique_market_conditions":len(sims),
                "unpriceable_after_assumed_slippage":len(eligible)-len(sims),
                "hypothetical_hold_pnl_usd":round(sum(pnl for _,pnl in sims),5)
                                                if denominator else None,
                "hypothetical_hold_roi":round(
                    sum(pnl for _,pnl in sims)/denominator,6)
                    if denominator else None,
                "actual_net_copy_roi":None,
            }
        info["final_positions_excluded_from_model"]=dict(declines)
        info["unique_market_conditions_with_verifiable_original_quote"]=len(eligible)
        info["descriptive_30_unique_market_conditions_reached"]=len(eligible)>=30
        info["hypothetical_models_for_final_quotes_only"]=scenarios
        info["actual_copy_roi"]=None
        info["independence_and_matched_comparison_not_proven"]=True
        result_groups[label]=info
    return {
        "schema":"v33_reason_aware_sports_quote_outcomes_1",
        "source_checkpoint_runs":(checkpoint.get("meta") or {}).get("completed_runs"),
        "source_checkpoint_saved_at":checkpoint.get("last_saved_at"),
        "archived_rows":len(rows),
        "invalid_identity_rows_excluded":rejected_identity,
        "unique_conditions_across_all_reason_groups":len(conditions),
        "colliding_conditions_excluded_from_all_model_groups":len(collided),
        "overall_legacy_or_unproven_reason_observations":
            len(groups.get("legacy_or_unproven_reason",[])),
        "by_proven_reason_or_legacy_group":result_groups,
        "all_source_results_observational_only":True,
        "sport_event_independence_not_verified":True,
        "different_entry_time_odds_and_settlement_censoring_present":True,
        "real_fee_and_fill_evidence":False,
        "causal_benefit_from_changing_any_risk_gate":None,
        "allow_live_wallet_promotion_or_threshold_relaxation":False,
        "orders":0,
        "telegram_sends":0,
    }


def main():
    report=compile_report(json.loads(INPUT.read_text()))
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    view={key:value for key,value in report.items()
          if key!="by_proven_reason_or_legacy_group"}
    view["by_proven_reason_or_legacy_group"]={
        k:{"observed_position_keys":v["observed_position_keys"],
           "final_classifications":v["final_classifications"],
           "quoted_final_unique_conditions":
               v["unique_market_conditions_with_verifiable_original_quote"],
           "simulated_fee_5pct_hold_roi":v[
               "hypothetical_models_for_final_quotes_only"][
               "assumed_5pct_fee"]["hypothetical_hold_roi"]}
        for k,v in report["by_proven_reason_or_legacy_group"].items()}
    print("V33 RISK REASON OUTCOME AUDIT",json.dumps(view,sort_keys=True))
    print("No live orders or Telegram; no causal gate effect inferred.")


if __name__=="__main__":
    main()
