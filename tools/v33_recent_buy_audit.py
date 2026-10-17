#!/usr/bin/env python3
"""Fast read-only truth audit: newest original BUYs, never profitable exits first.

Samples by historical BUY timestamp BEFORE looking at final outcomes.
This is not the "last 30 settled" sample; pending and absent positions remain
explicitly in denominator, and category/token mismatch is flagged.
"""
import collections
import json
import os
from tools import v33_result_audit as audit

WALLETS=[
 "0x300317934bfe0cc8740affc7425b0cd3772530f5",
 "0x6b1dbe91847aa95386fc63669ff910e68469a01a",
 "0x31550df5402226b825f900a31659a05d809d268e",
]
WALLETS=[w.strip().lower() for w in
         os.getenv("V33_RECENT_AUDIT_WALLETS",
                   ",".join(WALLETS)).split(",")
         if audit.api.WALLET_RE.fullmatch(w.strip().lower())]
MAX_BUYS=max(10,min(50,int(os.getenv("V33_RECENT_AUDIT_MAX_BUYS","50"))))


def scan(wallet,trades,positions,trades_complete,positions_complete,
         classifier=audit.classify,cap=MAX_BUYS):
    picks=audit.candidate_positions(trades)[:cap]
    indexed=collections.defaultdict(list)
    for pos in positions:
        condition=audit.safe_condition(pos.get("condition_id"))
        if condition:
            indexed[condition].append(pos)
    status=collections.Counter()
    position_status=collections.Counter()
    losses_held=0
    records=[]
    gamma,resolutions={},{}
    for condition,token,at,sold in picks:
        entries=indexed.get(condition) or []
        if not entries:
            label="unknown_position_absent"
        elif len({str(v.get("token_id") or "") for v in entries})!=1:
            label="unknown_multiple_outcomes"
        elif str(entries[0].get("token_id") or "") != token:
            label="unknown_token_not_held"
        else:
            for group in {str(v.get("status") or "") for v in entries}:
                position_status[group]+=1
            if any(str(v.get("status"))=="REDEEMABLE" and
                   float(v.get("realized_pnl") or 0)+
                   float(v.get("unrealized_pnl") or 0) <= -0.01
                   for v in entries):
                losses_held+=1
            try:
                label,settled=classifier(condition,token,gamma,resolutions)
                if settled is not None and at > settled:
                    label="unknown_buy_after_settlement"
            except Exception:
                label="api_error"
        status[label]+=1
        records.append({"condition":condition,"original_buy_at":at,
                        "source_sold":sold,
                        "status":label})
    return {"wallet":wallet,"selection":"newest_first_BUY_regardless_CLOSED_or_REDEEMABLE",
            "original_Buys_in_trade_window":len(audit.candidate_positions(trades)),
            "newest_BUYs_examined":len(picks),
            "trades_complete_180d":bool(trades_complete),
            "positions_complete":bool(positions_complete),
            "outcomes":dict(status),
            "closed_or_redeemable_status_scanned":dict(position_status),
            "still_held_negative_positions_in_sample":losses_held,
            "observed_verified_sports_wins":status["win"],
            "observed_verified_sports_losses":status["loss"],
            "true_sports_winrate_certified":False,
            "notes":"Buy-time cohort without outcome selection. Unknown/pending/outside "
                    "history statuses explicitly preserved. Single BUY side only; "
                    "no real follower ASK or fills. Win/loss of verified subset "
                    "must not be called all-time or last-30 settled winrate.",
            "rows":records}


def main():
    for wallet in WALLETS:
        trades,tc=audit.fetch_wallet_trades(wallet)
        positions,pc=audit.fetch_position_history(wallet)
        result=scan(wallet,trades,positions,tc,pc)
        result.pop("rows")
        print("RECENT FIRST-BUY COHORT",json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
