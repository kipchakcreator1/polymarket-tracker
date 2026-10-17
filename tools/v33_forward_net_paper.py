#!/usr/bin/env python3
"""Forward ONLY: settle actual-time $5 visible-ask BUY paper quotes.

Official outcome source: independent Polymarket Data API v2 token/resolution
audit; no retrospective CLOB fill reconstruction and absolutely no orders.

Polymarket published 2026 maker/taker fee formula:
  USDC equivalent = shares * feeRate * price * (1 - price).
Buy taker fees are collected in SHARES. Here fee-adjusted winning payout
is (shares - fee_usdc_equiv / price), loser payout is zero. Fee schedules
vary by individual market; using 0.05 is a *scenario*, not proof of actual
charged fee. Reports also show a zero-fee and 0.07 stress scenario and 5%
entry-price deterioration without claiming that price was executable.
"""
import json
import math
from collections import defaultdict
from pathlib import Path

STATE=Path("research/v33_signal_state.json")
TRUTH=Path("research/v33_truth_report.json")
OUTPUT=Path("research/v33_forward_net_paper.json")
STAKE_MAX=100
FEE_RATES=(0.0,0.05,0.07)
PRICE_WORSE_MULTIPLIER=1.05


def valid_float(raw,low,high):
    if isinstance(raw,bool):
        return None
    try:
        value=float(raw)
    except (ValueError,TypeError,OverflowError):
        return None
    return value if math.isfinite(value) and low<value<high else None


def paper_pnl(stake,price,won,fee_rate=0.05,entry_price_multiplier=1):
    """Hypothetical $-stake book BUY sweep followed by hold to final payout.

    Fee modeled as shares deducted on taker BUY, rounded only for report.
    No exits before settlement, network charges, fill slippage or rebates.
    """
    usd=valid_float(stake,0,STAKE_MAX+1)
    p=valid_float(price,0,1)
    r=valid_float(fee_rate,-0.000001,0.100001)
    worse=valid_float(entry_price_multiplier,0.999,1.101)
    if usd is None or p is None or r is None or worse is None or not isinstance(won,bool):
        return None
    effective=p*worse
    if effective>=1:
        return None
    gross_shares=usd/effective
    fee_shares=gross_shares*r*(1-effective)
    net_shares=gross_shares-fee_shares
    return (net_shares if won else 0.0)-usd


def evaluate(ledger,official):
    saved={}
    for feed in ("alerts","shadows"):
        for row in ((official.get(feed) or {}).get("rows") or []):
            if not isinstance(row,dict):
                continue
            key="|".join([str(row.get("wallet") or ""),
                          str(row.get("condition") or ""),
                          str(row.get("token_id") or "")])
            saved[key]=row
    snapshots={}
    for stream in ("signal_evidence","shadow_evidence"):
        for key,quote in (ledger.get(stream) or {}).items():
            if isinstance(quote,dict) and quote.get("topic")=="sports":
                existing=snapshots.get(key)
                if existing is not None:
                    # Same wallet/condition/token in both streams should
                    # count ONCE as Telegram if server accepted the alert.
                    if stream=="signal_evidence":
                        snapshots[key]=(quote,"telegram_sent")
                else:
                    snapshots[key]=(quote, "telegram_sent" if
                                    stream=="signal_evidence" else "shadow_only")
    rows=[]
    stats=defaultdict(int)
    financial=defaultdict(lambda:{"stake":0., "model_pnl":0.,
                                  "gross_pnl":0.,"fee_worst_pnl":0.,
                                  "adverse_5pct_pnl":0., "count":0})
    for key,(quote,stream) in sorted(snapshots.items()):
        reason=None
        ask=valid_float(quote.get("hypothetical_copy_ask"),0,1)
        stake=valid_float(quote.get("hypothetical_copy_stake_usd"),0,STAKE_MAX+1)
        qty=valid_float(quote.get("hypothetical_copy_shares"),0,1000000000)
        when=quote.get("book_quoted_at",quote.get(
            "quoted_at",quote.get("alert_at")))
        try:
            when=int(when)
        except (ValueError,TypeError,OverflowError):
            when=0
        result=saved.get(key)
        outcome=result.get("result") if isinstance(result,dict) else None
        settled=result.get("settled_at") if isinstance(result,dict) else None
        if ask is None or stake is None or not when:
            reason="invalid_or_missing_quote"
        elif qty is None:
            reason="missing_book_share_depth_evidence"
        elif not math.isclose(ask*qty,stake,rel_tol=.025,abs_tol=.025):
            reason="inconsistent_book_shares_vwap"
        elif when < int(quote.get("source_trade_at") or 0)-300:
            reason="quoted_before_source_trade"
        elif outcome in ("win","loss"):
            if not settled or when>=int(settled):
                reason="entry_not_before_verified_settlement"
        elif outcome in ("not_verified_sport","push_or_partial"):
            reason="nonstandard_or_not_verified_sport"
        else:
            reason="awaiting_independent_official_settlement"
        row={"wallet":quote.get("wallet"),
             "condition":quote.get("condition"),"token_id":quote.get("token_id"),
             "stream":stream,"status":reason or "verified_settled_paper",
             "book_quoted_at":when,"ask_vwap":ask,
             "stake_usd":stake,"visible_book_shares":qty,
             "final_outcome":outcome,
             "decision":quote.get("decision","sent_telegram" if
                                    stream=="telegram_sent" else "shadow"),
             "fees_are_modeled_not_reconciled":True,
             "real_trading_fill":False}
        stats[stream]+=1
        stats[row["status"]]+=1
        if reason is None:
            won=outcome=="win"
            result_set={}
            for rate in FEE_RATES:
                result_set[f"pnl_fee_rate_{rate:.2f}"]=round(
                    paper_pnl(stake,ask,won,rate),5)
            result_set["pnl_fee_0.05_plus_5pct_worse_entry"]=round(
                paper_pnl(stake,ask,won,0.05,PRICE_WORSE_MULTIPLIER),5)
            row["hypothetical_pnl_usd"]=result_set
            record=financial[stream]
            record["stake"]+=stake
            record["model_pnl"]+=result_set["pnl_fee_rate_0.05"]
            record["gross_pnl"]+=result_set["pnl_fee_rate_0.00"]
            record["fee_worst_pnl"]+=result_set["pnl_fee_rate_0.07"]
            record["adverse_5pct_pnl"]+=result_set[
                "pnl_fee_0.05_plus_5pct_worse_entry"]
            record["count"]+=1
        rows.append(row)
    summary={}
    for stream in ("telegram_sent","shadow_only"):
        rec=financial[stream]
        total=rec["stake"]
        summary[stream]={
            "qualifying_resolved_observations":rec["count"],
            "hypothetical_book_stake_usd":round(total,5),
            "fee_free_model_hold_pnl_usd":round(rec["gross_pnl"],5) if total else None,
            "fee_0.05_model_hold_pnl_usd":round(rec["model_pnl"],5) if total else None,
            "fee_0.07_model_hold_pnl_usd":round(rec["fee_worst_pnl"],5) if total else None,
            "fee_0.05_plus_5pct_worse_entry_pnl_usd":round(
                rec["adverse_5pct_pnl"],5) if total else None,
            "fee_0.05_model_roi":round(rec["model_pnl"]/total,6) if total else None,
            "actual_filled_orders":0,
            "actual_net_copy_roi":None
        }
    return {
        "schema":"v33_forward_net_paper_1",
        "official_source_updated_at":official.get("updated_at"),
        "forward_only_no_backfilled_quotes":True,
        "quote_coverage":dict(stats),
        "by_stream":summary,
        "positions":rows,
        "settled_outcomes_ever_unpriced_do_not_count":True,
        "fees_assumptions":{"formula":"fee_usdc_equiv=shares*r*p*(1-p)",
             "buy_fee_deducted_in_shares":True,
             "scenarios_rates":list(FEE_RATES),
             "published_sports_example_rate":0.05,
             "note":"Per-market fee/rate/exponent and any rebates must be "
                    "checked on market metadata; these are modeled, not "
                    "verified collected fees. Fees on buy are paid in shares."},
        "disclaimer":"A $5 depth/VWAP snapshot at notification time is not "
           "a fill guarantee. Real order execution, fee receipts, sell/exit "
           "history, and actual net profit remain unknown. Report NULL when "
           "no eligible official resolved quote is available."
    }


def main():
    state=json.loads(STATE.read_text())
    truth=json.loads(TRUTH.read_text())
    report=evaluate(state,truth)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,
                         separators=(",",":"))+"\n")
    print("V33 FORWARD NET PAPER",json.dumps({
        "source":report["official_source_updated_at"],
        "coverage":report["quote_coverage"],
        "streams":report["by_stream"]},sort_keys=True))


if __name__=="__main__":
    main()
