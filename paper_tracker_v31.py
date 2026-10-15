#!/usr/bin/env python3
"""3.1 paper-only portfolio for Polymarket tracker v3.

- Never signs/orders/writes to a trading service; reads CLOB asks and Gamma metadata.
- Records exactly one hypothetical $1 buy per (wallet, condition), if a clean,
  confirmed BUY alert was actually delivered and the current book is quotable.
- Evaluates outcome ONLY after UMA resolution status is resolved/settled AND
  exact 0/1 outcome token payout is available. No prices invented or backfilled.
- Fees are NOT known; reports gross and a separately labeled 1% hypothetical
  friction sensitivity (not a claim about actual fee charges).
"""
import collections
import datetime as dt
import html
import json
import os
import time

import smart_tracker_v2 as v2

GAMMA='https://gamma-api.polymarket.com'
CLOB='https://clob.polymarket.com'
STAKE=1.0
FRICTION_RATE=float(os.getenv('PAPER_FRICTION_PCT','0.01'))  # assumed $1*1%; NOT fee estimate
SETTLEMENT_CHECKS=int(os.getenv('PAPER_SETTLEMENT_CHECKS','12'))
MAX_PAPER_RECORDS=int(os.getenv('PAPER_MAX_RECORDS','2000'))


def portfolio(state):
    p=state.setdefault('paper_v31',{'records':{}, 'skipped':{}, 'started_at':int(time.time())})
    p.setdefault('records',{})
    p.setdefault('skipped',{})
    return p


def to_list(value):
    if isinstance(value,list): return value
    if isinstance(value,str):
        try:
            item=json.loads(value)
            return item if isinstance(item,list) else []
        except (ValueError,TypeError): return []
    return []


def get_market(condition):
    rows=v2.request_json('/markets',{'condition_ids':condition,'limit':5},base=GAMMA)
    if not isinstance(rows,list): raise ValueError('Gamma market response is not list')
    for m in rows:
        if str(m.get('conditionId','')).lower()==condition.lower(): return m
    raise ValueError('Gamma did not return exact market condition')


def market_token_index(m,token):
    ids=[str(x) for x in to_list(m.get('clobTokenIds'))]
    return ids.index(str(token)) if str(token) in ids else None


def is_final(m):
    # Closing a market != settlement. Do not use an apparent 99%-100% quote as an outcome.
    if m.get('closed') is not True: return False
    if str(m.get('umaResolutionStatus','')).lower() not in ('resolved','settled'):
        return False
    prices=to_list(m.get('outcomePrices'))
    ids=to_list(m.get('clobTokenIds'))
    if len(prices)!=len(ids) or len(prices)<2: return False
    try: values=[float(x) for x in prices]
    except (ValueError,TypeError): return False
    # Reject ambiguous/canceled/nonbinary settlement cases and stale book estimates.
    return values.count(1.0)==1 and values.count(0.0)==len(values)-1


def book_simulation(token,stake=STAKE):
    """Hypothetical $1 sweep of visible ask levels, not a real order/execution.

    Ask sizes represent shares. No slippage not shown in snapshot is modeled.
    Returns None instead of using an unsupported last-trade price.
    """
    book=v2.request_json('/book',{'token_id':str(token)},base=CLOB)
    if not isinstance(book,dict): raise ValueError('invalid book')
    if str(book.get('asset_id'))!=str(token): raise ValueError('book token mismatch')
    asks=[]
    for level in book.get('asks',[]):
        try: price=float(level['price']);qty=float(level['size'])
        except (TypeError,ValueError,KeyError): continue
        if 0<price<1 and qty>0: asks.append((price,qty))
    asks.sort(key=lambda x:x[0])
    if not asks: return None
    bids=[]
    for level in book.get('bids', []):
        try: price=float(level['price']);qty=float(level['size'])
        except (TypeError,ValueError,KeyError): continue
        if 0<price<1 and qty>0: bids.append(price)
    best_bid=max(bids) if bids else None
    # Unknown bid side => spread cannot be verified, so v3.3 will skip.
    spread=(asks[0][0]-best_bid) if best_bid is not None else None
    remaining=stake;shares=0.
    for price,available in asks:
        take=min(available,remaining/price)
        shares+=take
        remaining-=take*price
        if remaining<1e-7: break
    if remaining>1e-6 or shares<=0: return None
    ask=asks[0][0]
    min_order=v2.asfloat(book.get('min_order_size'),0)
    return {'price':round(stake/shares,8),'shares':round(shares,8),
            'best_ask':ask,'best_bid':best_bid,'spread':spread,
            'min_order_shares':min_order,
            'one_dollar_meets_min':bool(shares+1e-8>=min_order) if min_order>0 else None}


def paper_key(wallet,condition): return wallet.lower()+'|'+condition.lower()


def prepare(state,g,kind,holdings,now):
    """Nonmutating candidate probe, called before sending the BUY alert.

    Returns (candidate | None, human-readable note). """
    if kind!='BUY':return None,None
    p=portfolio(state)
    key=paper_key(g['wallet'],g['condition'])
    if key in p['records']:
        return None,'📒 Paper: market already recorded for this wallet'
    if len(p['records'])>=MAX_PAPER_RECORDS:
        return None,'📒 Paper: portfolio capacity reached'
    buying=[(token,out) for token,out in g['outcomes'].items() if out['buy_qty']>0]
    if len(buying)!=1 or holdings is None: return None,'📒 Paper: ambiguous/unverified BUY — skipped'
    token,out=buying[0]
    if out['sell_qty']>0 or holdings.get(token,0)<0.01 or any(
        shares>=0.01 for other,shares in holdings.items() if other!=token):
        return None,'📒 Paper: opposite/missing/exit position — skipped'
    try:
        market=get_market(g['condition'])
        if market.get('closed') is True or market.get('acceptingOrders') is False:
            return None,'📒 Paper: market not accepting trades — skipped'
        if market_token_index(market,token) is None:
            return None,'📒 Paper: outcome token missing in Gamma — skipped'
        quote=book_simulation(token)
        if quote is None:
            return None,'📒 Paper: no $1 ask-side liquidity — skipped'
    except Exception as exc:
        print('PAPER ENTRY WARNING',g['wallet'][:12],g['condition'][:12],type(exc).__name__,str(exc)[:120])
        return None,'📒 Paper: price unavailable — not recorded'
    source_price=out['buy_usd']/out['buy_qty'] if out.get('buy_qty',0)>0 else None
    record={'wallet':g['wallet'],'condition':g['condition'],'token':token,
            'title':g['title'],'outcome':out['label'],'url':g.get('url',''),
            'alert_ts':now,'last_source_fill_ts':int(g['last']),
            'stake':STAKE,'shares':quote['shares'],'avg_price':quote['price'],
            'source_avg_buy_price':round(source_price,6) if source_price is not None else None,
            'source_to_paper_price_change':round(quote['price']-source_price,6) if source_price is not None else None,
            'best_ask':quote['best_ask'],'min_order_shares':quote['min_order_shares'],
            'feasible_1usd':quote['one_dollar_meets_min'],
            'status':'OPEN','last_checked':0}
    feasible='minimum order met' if quote['one_dollar_meets_min'] else ('below market minimum' if quote['one_dollar_meets_min'] is False else 'min order unknown')
    note=(f'📒 Paper $1: quote ${quote["price"]:.3f}, {quote["shares"]:.2f} shares '
          f'({feasible}); NOT an actual bet')
    return record,note


def commit(state,record):
    """Only persist after the corresponding message has been SENT successfully."""
    if record:
        p=portfolio(state)
        p['records'].setdefault(paper_key(record['wallet'],record['condition']),record)


def refresh_settlements(state,now):
    p=portfolio(state)
    due=collections.defaultdict(list)
    for rec in p['records'].values():
        if rec['status']!='OPEN':continue
        if now-rec.get('last_checked',0)<1800:continue  # no repeated requests on manual triggers
        due[rec['condition']].append(rec)
    # Fair scan: oldest checked first. One query per market.
    sorted_conds=sorted(due,key=lambda c:min(r.get('last_checked',0) for r in due[c]))
    checked=settled=errors=0
    for condition in sorted_conds[:max(0,SETTLEMENT_CHECKS)]:
        checked+=1
        # Never count an API failure as a resolution or hide it under green success.
        try:
            market=get_market(condition)
        except Exception as exc:
            errors+=1
            print('PAPER SETTLEMENT WARNING',condition[:12],type(exc).__name__,str(exc)[:120])
            for rec in due[condition]:rec['last_checked']=now
            continue
        for rec in due[condition]:
            rec['last_checked']=now
            if not is_final(market):continue
            idx=market_token_index(market,rec['token'])
            if idx is None:errors+=1;continue
            payout=float(to_list(market['outcomePrices'])[idx])
            rec.update(status='RESOLVED',closed_ts=now,payout_per_share=payout,
                       pnl_gross=round(rec['shares']*payout-rec['stake'],6),
                       pnl_after_assumed_friction=round(rec['shares']*payout-rec['stake']-rec['stake']*FRICTION_RATE,6))
            settled+=1
    return {'checked_markets':checked,'resolved':settled,'errors':errors}


def report(state,extra=None):
    p=portfolio(state)
    recs=list(p['records'].values())
    resolved=[r for r in recs if r['status']=='RESOLVED']
    gross=sum(r.get('pnl_gross',0) for r in resolved)
    stressed=sum(r.get('pnl_after_assumed_friction',0) for r in resolved)
    win=sum(r.get('payout_per_share',0)==1 for r in resolved)
    distinct=len({r['condition'] for r in resolved})
    feasible_resolved=[r for r in resolved if r.get('feasible_1usd') is True]
    feasible_gross=sum(r.get('pnl_gross',0) for r in feasible_resolved)
    unfeasible=sum(r.get('feasible_1usd') is False for r in recs)
    fields=(f'PAPER v3.1: positions={len(recs)} open={len(recs)-len(resolved)} '
            f'resolved={len(resolved)} distinct_resolved_markets={distinct} '
            f'wins={win}/{len(resolved)} gross_usd={gross:+.2f} '
            f'after_assumed_1pct_friction_usd={stressed:+.2f} '
            f'feasible_resolved={len(feasible_resolved)} feasible_gross_usd={feasible_gross:+.2f} '
            f'below_minimum_for_1usd={unfeasible}')
    if extra:
        fields+=' settlement_checked={checked_markets} newly_resolved={resolved} settlement_errors={errors}'.format(**extra)
    return fields


def wallet_digest(state,limit=6):
    """Descriptive counts, sorted by sample size; no ranking/recommendation."""
    groups=collections.defaultdict(list)
    for rec in portfolio(state)['records'].values():
        groups[rec['wallet']].append(rec)
    rows=[]
    for wallet,recs in groups.items():
        completed=[r for r in recs if r.get('status')=='RESOLVED']
        if not completed: continue
        count=len(completed)
        won=sum(r.get('payout_per_share')==1. for r in completed)
        pnl=sum(r.get('pnl_gross',0) for r in completed)
        name=state.get('wallets',{}).get(wallet,{}).get('name') or wallet[:12]
        rows.append((count,name,won,pnl))
    rows.sort(key=lambda r:(-r[0],r[1]))
    if not rows:return 'Paper wallet breakdown: no settled outcomes yet.'
    lines=['Paper wallet breakdown (sample sizes, not recommendations):']
    for n,name,wins,pnl in rows[:limit]:
        lines.append(f'{name[:24]}: resolved {n}, profitable resolutions {wins}/{n}, gross ${pnl:+.2f}')
    return '\n'.join(lines)


def leaderboard(state,limit=10):
    groups=collections.defaultdict(list)
    for r in portfolio(state)['records'].values():groups[r['wallet']].append(r)
    rows=[]
    for wallet,allr in groups.items():
        finished=[r for r in allr if r['status']=='RESOLVED']
        if not finished: continue
        gross=sum(r['pnl_gross'] for r in finished)
        rows.append((wallet,len(finished),round(gross,2),sum(r['payout_per_share']==1 for r in finished)))
    rows.sort(key=lambda x:(-x[1],-x[2]))  # not a performance ranking, prefer sufficient sample
    return rows[:limit]
