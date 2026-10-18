#!/usr/bin/env python3
"""Read-only sports OPEN-position opportunities, explicitly NOT fresh BUY.

Runs inside v3.3's existing monitor with the same vetted wallet shortlist,
market/sports/politics restrictions, CLOB $5 depth gates, and portfolio risk.
Scans only a bounded rotation each poll; no historical replay or automatic
order and max 3 new OPEN notifications/hour. Zero fabricated price fills.
"""
import html
import math
import os
import time

from tools import v33_signal_bot as bot

WALLETS_PER_POLL = max(1,min(8,int(os.getenv("V33_OPEN_SCAN_PER_POLL","4"))))
ALERTS_PER_HOUR = max(1,min(5,int(os.getenv("V33_OPEN_MAX_ALERTS_PER_HOUR","3"))))
MAX_POSITION_AGE = 48*3600
# Match the existing $35 verified BUY floor; do not promote small/dust holds.
MIN_POSITION_COST = max(35.,float(os.getenv("V33_OPEN_MIN_POSITION_USD","35")))
MIN_BEFORE_GAME = 10*60
OPEN_REPEAT_WINDOW = 24*3600
OPEN_JOURNAL_WINDOW = 45*86400


def available_open_position(position, now):
    """Never infer fresh BUY from a held position or from missing fields."""
    if not isinstance(position,dict) or position.get("status")!="OPEN" or (
            position.get("redeemable") is not False):
        return None
    condition=str(position.get("condition_id") or "").lower()
    token=str(position.get("token_id") or "")
    if not bot.v32.discovery.CONDITION_RE.fullmatch(condition) or not token:
        return None
    try:
        last=bot.position_activity_timestamp(position.get("last_event_at"),now)
    except ValueError:
        return None
    if not 0 <= now-last <= MAX_POSITION_AGE:
        return None
    shares=bot.v2.asfloat(position.get("current_size"),float("nan"))
    cost=bot.v2.asfloat(position.get("entry_cost_usdc"),float("nan"))
    if not (math.isfinite(shares) and math.isfinite(cost) and
            shares >= .01 and cost >= MIN_POSITION_COST):
        return None
    basis=cost/shares
    if not bot.MIN_PRICE <= basis <= bot.MAX_PRICE:
        return None
    slug=str(position.get("event_slug") or "")
    url=bot.v32.slug_link({"event_slug":slug})
    return {
        "wallet":str(position.get("proxy_wallet") or "").lower(),
        "condition":condition,"token":token,
        "title":str(position.get("title") or "Спортивное событие")[:150],
        "outcome":str(position.get("outcome") or "Исход")[:60],
        "url":url,"last_activity_at":last,
        "cost_basis":basis,"position_cost_usd":cost,
        "shares_held":shares
    }


def open_message(p, selected, quote, checked_at):
    name=html.escape(str(selected[p["wallet"]].get("name") or
                         p["wallet"][:12]),quote=False)
    title=html.escape(p["title"])
    outcome=html.escape(p["outcome"])
    href=html.escape(p["url"],quote=True)
    link=(f'<a href="{href}">Рынок</a> | ' if href else "")
    link+=f'<a href="https://polymarket.com/profile/{p["wallet"]}">Кошелёк</a>'
    elapsed=max(0,(checked_at-p["last_activity_at"])//60)
    return (
        "🔎 <b>v3.3 • ОТКРЫТАЯ СПОРТИВНАЯ ПОЗИЦИЯ</b>\n"
        "⛔ <b>Это НЕ новый BUY трейдера.</b>\n"
        f"👤 {name}\n🏟 {title}\n🎯 {outcome}\n"
        f"💼 Трейдер всё ещё держит позицию (издержки ≠ прогноз).\n"
        f"📆 Последняя активность по позиции: {elapsed} мин назад\n"
        f"💵 Историческая средняя себестоимость: {p['cost_basis']:.3f}\n"
        f"📕 Текущий модельный вход по стакану на $5: {quote['price']:.3f}\n"
        f"📊 Видимый спред: {quote['spread']:.3f}; "
        f"акций в модельной покупке: {quote.get('shares',0):.2f}\n"
        f"🔗 {link}\n"
        "⚠️ Не доказано преимущество, нет исполненной сделки. "
        "Цена и ликвидность могут измениться. "
        "Это исследовательское наблюдение, не рекомендация к ставке."
    )


def scan(state, selected, now, *, clock=None):
    """Scan a small stable rotation, risk-veto first; send only fresh market quotes."""
    if not selected or bot.MARKET_FOCUS!="sports_btc":
        return 0
    order=list(selected)
    start=int(state.get("open_wallet_cursor") or 0)%len(order)
    chosen=[order[(start+i)%len(order)]
            for i in range(min(WALLETS_PER_POLL,len(order)))]
    state["open_wallet_cursor"]=(start+len(chosen))%len(order)
    checked_at=max(int(now),int(clock() if clock else time.time()))
    recent=[int(x) for x in (state.get("open_alert_times") or [])
            if 0<=checked_at-int(x)<3600]
    state["open_alert_times"]=recent
    sent=state.setdefault("open_sent",{})
    evidence=state.setdefault("open_position_evidence",{})
    accepted=0
    for wallet in chosen:
        if len(recent)>=ALERTS_PER_HOUR:
            bot.mark(state,"open_hourly_cap")
            break
        if wallet in bot.QUARANTINE_WALLETS:
            continue
        try:
            risk=bot.recent_form_reason(state,wallet,checked_at)
            if risk:
                bot.mark(state,"open_recent_risk_veto")
                continue
            # A COMPLETE live-portfolio snapshot is required: same as BUY.
            positions=bot.fetch_open_positions(wallet)
            snapshot=bot.active_form(positions)
            snapshot["checked_at"]=checked_at
            state.setdefault("active_form_cache",{})[wallet]=snapshot
            active_risk=bot.active_form_reason(state,wallet,checked_at)
            if active_risk:
                bot.mark(state,"open_portfolio_risk_veto")
                continue
        except Exception as exc:
            bot.mark(state,"open_risk_api_incomplete")
            print("V33 OPEN RISK WARNING",wallet[:12],
                  type(exc).__name__,str(exc)[:100])
            continue
        by_condition={}
        candidates=[]
        for raw in positions:
            p=available_open_position(raw,checked_at)
            if p is None or p["wallet"]!=wallet:
                continue
            by_condition.setdefault(p["condition"],set()).add(p["token"])
            candidates.append(p)
        candidates.sort(key=lambda x:-x["last_activity_at"])
        for p in candidates[:5]:
            if len(recent)>=ALERTS_PER_HOUR:
                break
            condition=p["condition"]
            token=p["token"]
            if len(by_condition.get(condition,()))!=1:
                bot.mark(state,"open_hedged_position_veto")
                continue
            dedupe="|".join((wallet,condition,token))
            if 0<=checked_at-int(sent.get(dedupe) or 0)<OPEN_REPEAT_WINDOW:
                continue
            if 0<=checked_at-int(state.get("sent",{}).get(dedupe) or 0)<OPEN_REPEAT_WINDOW:
                continue  # already sent as a NEW BUY, no repeat as OPEN
            if wallet+"|"+condition in state.get("pending",{}):
                continue  # a recent BUY is being processed independently
            group={"wallet":wallet,"condition":condition,"title":p["title"],
                   "url":p["url"],"last":p["last_activity_at"]}
            try:
                if bot.is_btc_market(group):
                    continue  # sports only, NO BTC under open-position path
                if bot.market_focus_reason(state,group,checked_at):
                    continue
                record=state.get("market_focus_cache",{}).get(condition) or {}
                # Sports *game* must still be ahead; never late/in-play.
                start_at=record.get("game_start")
                if (not isinstance(start_at,(int,float)) or
                        start_at-checked_at<MIN_BEFORE_GAME):
                    bot.mark(state,"open_game_start_unverified_or_started")
                    continue
                if bot.political_market_reason(state,group,checked_at):
                    continue
                cand={"token":token,"source":p["cost_basis"],
                      "usd":p["position_cost_usd"],"outcome":p["outcome"]}
                quote,reason=bot.check_trade(group,cand)
                if reason:
                    bot.mark(state,"open_"+reason)
                    continue
                if not (isinstance(quote,dict) and
                        bot.v2.asfloat(quote.get("shares"))>0 and
                        quote.get("spread") is not None):
                    bot.mark(state,"open_quote_incomplete")
                    continue
                last_check=max(checked_at,int(clock() if clock else time.time()))
                if last_check-start_at>=0 or start_at-last_check<MIN_BEFORE_GAME:
                    bot.mark(state,"open_game_started_before_send")
                    continue
                # Enforce freshness AFTER slow API/risk verification.
                if not bot.v32.market_accepts_orders(condition):
                    bot.mark(state,"open_market_closed_before_send")
                    continue
                receipt=bot.v2.send_telegram(open_message(
                    p,selected,quote,last_check))
                if not (isinstance(receipt,int) and receipt>0):
                    raise ValueError("Telegram API did not acknowledge OPEN message")
            except Exception as exc:
                bot.mark(state,"open_verification_or_send_error")
                print("V33 OPEN VERIFY WARNING",wallet[:12],
                      type(exc).__name__,str(exc)[:95])
                continue
            sent[dedupe]=last_check
            recent.append(last_check)
            evidence[dedupe]={"wallet":wallet,"condition":condition,
                "token_id":token,"topic":"sports","alert_kind":"open_position",
                "quoted_at":last_check,"hypothetical_copy_ask":quote["price"],
                "hypothetical_copy_shares":quote["shares"],
                "hypothetical_copy_stake_usd":bot.QUOTE_STAKE,
                "position_last_activity":p["last_activity_at"],
                "source_cost_basis_not_first_buy":p["cost_basis"],
                "note":"open-position study, not fresh source BUY, not a fill"}
            state["last_telegram_acceptance"]={
                "at":last_check,"message_id":receipt,"kind":"open_position",
                "note":"API accepted; client push/display is not verified"}
            bot.mark(state,"open_alerts_sent")
            accepted+=1
            break  # cap one OPEN alert per checked wallet
    state["open_alert_times"]=recent[-ALERTS_PER_HOUR:]
    # The Telegram ack timestamp is sampled AFTER the scan begins.
    # Pruning against the initial scan clock would drop every just-sent OPEN
    # whenever API verification takes even one second, allowing duplicates.
    state["open_sent"]={k:v for k,v in sent.items()
                        if -bot.EVIDENCE_FUTURE_SKEW_SECONDS <=
                        checked_at-int(v)<=30*86400}
    state["open_position_evidence"]=bot.keep_forward_evidence(
        evidence,checked_at,"quoted_at",limit=900)
    return accepted
