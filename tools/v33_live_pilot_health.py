#!/usr/bin/env python3
"""Read-only production v3.3 300->350 pilot health/funnel comparison.

Requires production snapshot checkout and the existing v3.3 selection function.
No API calls, writes to Telegram, or changes to cursor/risk state.
"""
import collections
import json
import os
import time
from pathlib import Path

from tools import v33_signal_bot as bot

INPUT = Path(os.getenv("V33_PILOT_INPUT_DIR", "v33-snapshot"))
OUTPUT = Path("artifacts/v33-350-live-pilot-health.json")
WINDOWS = (3600, 6*3600, 24*3600)


def positive_epoch(value, now):
    if isinstance(value, bool):
        return None
    try:
        n=int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if 0<n<=now+bot.EVIDENCE_FUTURE_SKEW_SECONDS else None


def selected_cohorts(source, now, selector=None):
    selector=selector or bot.select_wallets
    original=bot.MAX_WALLETS
    try:
        bot.MAX_WALLETS=300
        base=selector(source,now)
        bot.MAX_WALLETS=350
        larger=selector(source,now)
    finally:
        bot.MAX_WALLETS=original
    old=set(base)
    newer=set(larger)
    if not old or not old.issubset(newer):
        raise ValueError("Research pilot must preserve all of old 300")
    return old,newer-old,newer


def count_evidence(evidence, cohort, now, timestamp_field):
    result={}
    if not isinstance(evidence,dict):
        return result
    for window in WINDOWS:
        entries=[]
        for key, item in evidence.items():
            if not isinstance(key,str) or not isinstance(item,dict):
                continue
            wallet=key.split("|")[0].lower()
            t=positive_epoch(item.get(timestamp_field),now)
            if wallet in cohort and t is not None and now-t <= window:
                entries.append((key,item))
        result[f"{window//3600}h"]={
            "quoted_observations":len(entries),
            "unique_wallets":len({key.split("|")[0].lower() for key,_ in entries}),
            "unique_market_conditions":len({
                key.split("|")[1] for key,_ in entries if len(key.split("|"))>=2}),
            "marked_telegram_sent":sum(item.get("decision")=="sent_telegram"
                                       for _,item in entries),
            "marked_rejected_recent_form":sum(item.get("decision")=="blocked_recent"
                                              for _,item in entries),
            "other_decision_or_unknown":sum(
                item.get("decision") not in ("sent_telegram","blocked_recent")
                for _,item in entries),
        }
    return result


def sent_count(sent, cohort, now):
    out={}
    for window in WINDOWS:
        accepted=[]
        for key,value in (sent or {}).items():
            if not isinstance(key,str):
                continue
            epoch=positive_epoch(value,now)
            if (epoch is not None and now-epoch<=window and
                    key.split("|")[0].lower() in cohort):
                accepted.append(key)
        out[f"{window//3600}h"]={
            "saved_buy_dedupe_keys":len(accepted),
            "unique_wallets":len({key.split("|")[0].lower() for key in accepted}),
            "unique_conditions":len({key.split("|")[1] for key in accepted
                                     if len(key.split("|"))>=2}),
        }
    return out


def build(source,state,now=None,selector=None):
    now=int(time.time()) if now is None else int(now)
    if not isinstance(state,dict) or state.get("version")!=33:
        raise ValueError("Invalid runtime state")
    if not isinstance(state.get("cursors"),dict) or not isinstance(state.get("last_run"),dict):
        raise ValueError("Runtime cursor or last-run evidence unavailable")
    old, extra, enlarged=selected_cohorts(source,now,selector)
    saved={w.lower() for w in state["cursors"] if isinstance(w,str)}
    observed_last=state["last_run"]
    matched=bool(saved==enlarged and observed_last.get("watching")==len(enlarged))
    live_new=extra & saved
    evidence=state.get("shadow_evidence") or {}
    by_group={}
    for name, members in (("old_300_reconstructed",old),
                          ("additional_up_to_50",extra),
                          ("unknown_or_unselected",saved-enlarged)):
        by_group[name]={
            "reconstructed_member_count":len(members),
            "members_in_last_persisted_runtime":len(members & saved),
            "prospective_book_quote_decisions":count_evidence(
                evidence,members,now,"quoted_at"),
            "recent_buy_dedupe_keys":sent_count(state.get("sent"),members,now),
        }
    counts=observed_last.get("this_poll") or {}
    relevant=("eligible_source_buy_sports","book_verified_sports",
              "major_unredeemed_loss_cluster_90d","recent_form_request_error",
              "market_focus_request_error","alerts_sent","alerts_sports",
              "open_recent_risk_veto")
    return {
        "schema":"v33_350_monitor_live_health_1",
        "captured_at":now,
        "source_shortlist_observed_at":source.get("observed_at"),
        "source_reconstructed_old_count":len(old),
        "source_reconstructed_pilot_count":len(enlarged),
        "candidate_extra_wallets":len(extra),
        "actual_last_saved_cursor_count":len(saved),
        "actual_last_run_watching":observed_last.get("watching"),
        "last_saved_exactly_matches_reconstructed_350":matched,
        "new_research_group_present_in_saved_runtime":len(live_new),
        "last_poll_request_errors":observed_last.get("request_errors"),
        "last_poll_gaps":observed_last.get("poll_gaps"),
        "last_poll_new_fills_all_wallets":observed_last.get("new_fills"),
        "last_poll_buy_notifications_all_wallets":observed_last.get("alerts"),
        "last_poll_open_notifications_all_wallets":observed_last.get("open_position_alerts"),
        "last_poll_suppression_deltas":{k:counts.get(k,0) for k in relevant},
        "open_dedupe_keys_retained":len(state.get("open_sent") or {}),
        "by_reconstructed_cohort":by_group,
        "notification_delivery_not_verified":True,
        "source_market_pnl_and_outcome_not_verified":True,
        "risk_configuration_verified_by_this_read_only_report":False,
        "no_risk_gate_or_live_watchlist_modified_by_this_report":True,
        "no_fills_or_alerts_in_group_is_not_evidence_the_group_is_unprofitable":True,
        "no_api_requests_or_production_writes":True,
        "warnings":[
            "Old300 baseline reconstructed from CURRENT shortlist, not exact historical watch state.",
            "Sent keys are bounded 7-day dedupe, not complete Telegram log or client delivery.",
            "Quote observations are bounded to the last 900 and may be lost before harvest.",
            "A funnel stage count can exceed number of unique wallet trades.",
            "No causal performance evaluation from unequal wallet cohorts or periods.",
        ],
    }


def main():
    source=json.loads((INPUT/"research/v33_wallet_shortlist.json").read_text())
    state=json.loads((INPUT/"research/v33_signal_state.json").read_text())
    r=build(source,state)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(r,indent=2,ensure_ascii=False)+"\n")
    print("V33 PILOT HEALTH",json.dumps({k:v for k,v in r.items()
                                    if k not in ("warnings",)},sort_keys=True))
    print("Read-only comparison; NO Telegram, orders or watchlist changes")


if __name__=="__main__":
    main()
