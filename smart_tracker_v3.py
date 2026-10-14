#!/usr/bin/env python3
"""Polymarket Smart Tracker 3.1: READ-ONLY alerts + virtual $1 paper portfolio.

Needs smart_tracker_v2.py for wallet discovery/scoring (trading PnL only).
Reads cursor-paginated public /v2/trades, groups by condition ID (a MARKET,
not the umbrella event), checks /v2/positions, and persists state in repo.
NEVER places trades, requires NO wallet key or Polymarket credentials.
"""
import argparse
import collections
import datetime as dt
import hashlib
import html
import json
import os
import sys
import time
from pathlib import Path

import smart_tracker_v2 as v2
import paper_tracker_v31 as paper
import wallet_discovery_v32 as discovery
import copy_quality_v32 as quality
import wallet_forensics_v32 as forensics

VERSION = 31
STATE_FILE = 'state/smart_tracker_v3.json'
POLL_OVERLAP = 120
VERIFY_MARKET = os.getenv("VERIFY_MARKET", "1") == "1"
GROUP_SECONDS = int(os.getenv('GROUP_SECONDS', '60'))
MAX_SIGNAL_AGE = int(os.getenv('MAX_SIGNAL_AGE', '300'))
MIN_BUY_USD = float(os.getenv('MIN_BUY_USD', '100'))
MAX_BUY_PRICE = float(os.getenv('MAX_BUY_PRICE', '0.98'))  # Skip near-certain priced BUYs
COPY_QUALITY_GATE = os.getenv('COPY_QUALITY_GATE', '0') == '1'  # Opt-in; never trading
FORENSIC_SAMPLE_ENABLED = os.getenv('FORENSIC_SAMPLE_ENABLED', '0') == '1'
MAX_SUPPRESSED_RECORDS = int(os.getenv('MAX_SUPPRESSED_RECORDS', '2500'))
MIN_SELL_USD = float(os.getenv('MIN_SELL_USD', '20'))
MAX_ALERTS = int(os.getenv('MAX_ALERTS', '6'))
MAX_PER_WALLET = int(os.getenv('MAX_PER_WALLET', '2'))
MONITOR_MAX = int(os.getenv('MONITOR_MAX', '50'))
PAGES_PER_WALLET = int(os.getenv('PAGES_PER_WALLET', '10'))
FETCH_SIZE = 200
POSITION_PAGE_LIMIT = 500
POSITION_PAGE_CAP = 6


def fresh_state():
    s = v2.blank_state()
    s.update(version=VERSION, cursors={}, seen={}, pending={}, tracking={}, initialized=int(time.time()))
    return s


def read_state(path=STATE_FILE):
    p = Path(path)
    if p.exists():
        s = json.loads(p.read_text(encoding='utf-8'))
        if s.get('version') != VERSION:
            raise ValueError('State format/version mismatch. Do not overwrite old state silently.')
        for key in ('wallets','cursors','seen','pending','tracking'):
            if not isinstance(s.get(key),dict):
                raise ValueError('State corrupted: '+key)
        return s
    s=fresh_state()
    legacy=Path('state/smart_tracker_v2.json')
    if legacy.exists():
        old=json.loads(legacy.read_text(encoding='utf-8'))
        if old.get('version')==2 and isinstance(old.get('wallets'),dict):
            s['wallets']=old['wallets']
            s['discovered_at']=old.get('discovered_at',0)
            print('MIGRATION: imported v2 public wallet candidate list:',len(s['wallets']))
    return s


def num(value):
    return v2.asfloat(value)


def trade_id(t):
    # No log_index in this API. Completely identical rows can be ambiguous.
    fields=('proxy_wallet','transaction_hash','timestamp','token_id','side','size','price','condition_id')
    return hashlib.sha256('|'.join(str(t.get(k,'')) for k in fields).encode()).hexdigest()


def group_id(wallet, condition):
    return wallet+'|'+condition


def slug_link(t):
    slug=t.get('event_slug') or t.get('slug') or ''
    if isinstance(slug,str) and slug and len(slug)<220 and all(ch.isalnum() or ch in '-_' for ch in slug):
        return 'https://polymarket.com/event/'+slug
    return ''


def get_wallet_trades(wallet, since, now):
    """Cursor-paginated API v2. Refuse to advance cursor on incomplete reads."""
    cursor=None
    out=[]
    for page in range(PAGES_PER_WALLET):
        params={'user': wallet,'start':max(1,int(since)), 'end':int(now),
                'limit':FETCH_SIZE,'taker_only':'false'}
        if cursor:
            params['cursor']=cursor
        body=v2.request_json('/v2/trades',params)
        if not isinstance(body,dict) or not isinstance(body.get('data'),list) or not isinstance(body.get('pagination'),dict):
            raise RuntimeError('Unexpected /v2/trades shape; cursor NOT advanced')
        for t in body['data']:
            if str(t.get('proxy_wallet','')).lower()==wallet and t.get('side') in ('BUY','SELL'):
                out.append(t)
        pg=body['pagination']
        cursor=pg.get('next_cursor')
        if not pg.get('has_more'):
            return out
        if not cursor:
            raise RuntimeError('/v2/trades has_more but missing next_cursor')
    raise RuntimeError('Trade page cap reached; skipped wallet, cursor NOT advanced')


def get_positions(wallet, conditions):
    """Return condition -> token -> shares (complete or error).

    A position snapshot is NOT a historical reconstruction. Does not reveal
    hedging on another wallet or an exchange.
    """
    result=collections.defaultdict(dict)
    conditions=sorted(c for c in conditions if c)
    if not conditions:
        return {}
    for off in range(0,len(conditions),20):
        cohort=conditions[off:off+20]
        cursor=None
        for _ in range(POSITION_PAGE_CAP):
            args={'user':wallet,'condition':','.join(cohort),'status':'OPEN',
                  'limit':POSITION_PAGE_LIMIT,'filter_type':'TOKENS',
                  'filter_amount':'0.01','include_archived':'true'}
            if cursor:
                args['cursor']=cursor
            body=v2.request_json('/v2/positions',args)
            if not isinstance(body,dict) or not isinstance(body.get('data'),list) or not isinstance(body.get('pagination'),dict):
                raise RuntimeError('Unexpected /v2/positions shape')
            for x in body['data']:
                condition=str(x.get('condition_id') or '')
                token=str(x.get('token_id') or '')
                if condition in cohort and token:
                    result[condition][token]=num(x.get('current_size'))
            if not body['pagination'].get('has_more'):
                break
            cursor=body['pagination'].get('next_cursor')
            if not cursor:
                raise RuntimeError('/v2/positions cursor missing')
        else:
            raise RuntimeError('/v2/positions pagination cap reached')
    return dict(result)


def market_accepts_orders(condition):
    """Gamma market guard; fail closed on unknown/unavailable data.

    NOTE: open now does not imply the event is in the future, that the
    quoted trade price is still available, or that the market is liquid.
    """
    if not VERIFY_MARKET:
        return True
    rows = v2.request_json('/markets', {'condition_ids': condition},
                           base='https://gamma-api.polymarket.com')
    if not isinstance(rows, list):
        raise RuntimeError('Unexpected Gamma /markets response')
    matching = [r for r in rows if isinstance(r, dict) and
                str(r.get('conditionId', '')).lower() == condition.lower()]
    if len(matching) != 1:
        raise RuntimeError('Gamma market not found or ambiguous')
    m = matching[0]
    if not isinstance(m.get('active'), bool) or not isinstance(m.get('closed'), bool):
        raise RuntimeError('Gamma market active/closed status missing')
    if m['closed'] or not m['active']:
        return False
    if m.get('acceptingOrders') is False or m.get('enableOrderBook') is False:
        return False
    # Some Gamma data sets omit acceptingOrders. Prefer a positive confirmation
    # for research trade alerts rather than a misleading "available" label.
    if m.get('acceptingOrders') is not True:
        raise RuntimeError('Gamma acceptingOrders status unavailable')
    return True


def empty_group(wallet,condition,t):
    return {'wallet':wallet,'condition':condition,'first':int(num(t['timestamp'])),
            'last':int(num(t['timestamp'])),'title':str(t.get('title') or 'Market')[:180],
            'url':slug_link(t),'name':str(t.get('name') or t.get('pseudonym') or '')[:70],
            'outcomes':{}}


def add_fill(state,t,now):
    wallet=str(t.get('proxy_wallet') or '').lower()
    condition=str(t.get('condition_id') or '')
    token=str(t.get('token_id') or '')
    if not wallet or not condition or not token:
        return False
    key=trade_id(t)
    if key in state['seen']:
        return False
    state['seen'][key]=now
    gid=group_id(wallet,condition)
    g=state['pending'].setdefault(gid,empty_group(wallet,condition,t))
    ts=int(num(t.get('timestamp')))
    g['last']=max(g['last'],ts)
    g['first']=min(g['first'],ts)
    if t.get('title'): g['title']=str(t['title'])[:180]
    if slug_link(t): g['url']=slug_link(t)
    if t.get('name') or t.get('pseudonym'): g['name']=str(t.get('name') or t.get('pseudonym'))[:70]
    pos=g['outcomes'].setdefault(token,{'label':str(t.get('outcome') or '?')[:70],
                  'buy_usd':0.,'sell_usd':0.,'buy_qty':0.,'sell_qty':0.,'count':0})
    side=t['side']; shares=max(0.,num(t.get('size'))); usd=shares*num(t.get('price'))
    pos['buy_qty' if side=='BUY' else 'sell_qty']+=shares
    pos['buy_usd' if side=='BUY' else 'sell_usd']+=usd
    pos['count']+=1
    if side == 'BUY':
        # Passive price profile: deduplicated fills since monitoring began.
        # This is NOT the fraction of winning predictions nor lifetime history.
        stats=state.get('wallets',{}).get(wallet)
        if isinstance(stats,dict) and shares>0:
            stats['observed_buy_fills']=stats.get('observed_buy_fills',0)+1
            stats['observed_buy_usd']=round(stats.get('observed_buy_usd',0)+usd,4)
            if num(t.get('price'))>=MAX_BUY_PRICE:
                stats['observed_expensive_buy_fills']=stats.get('observed_expensive_buy_fills',0)+1
    return True


def scan_wallet(state,wallet,now):
    if wallet not in state['cursors']:
        # First monitoring of this wallet begins NOW, avoiding a flood of old history.
        state['cursors'][wallet]=now
        return 0,True
    if now-state['cursors'][wallet]>MAX_SIGNAL_AGE:
        print(f'GAP WARNING: {wallet[:12]} was not polled for {(now-state["cursors"][wallet])//60} min; expired fills may be missed')
    since=max(state['cursors'][wallet]-POLL_OVERLAP,now-MAX_SIGNAL_AGE)
    rows=get_wallet_trades(wallet,since,now)
    added=0
    for t in sorted(rows,key=lambda r:(int(num(r.get('timestamp'))),trade_id(r))):
        ts=int(num(t.get('timestamp')))
        if ts<since or ts>now+60:
            continue
        added+=int(add_fill(state,t,now))
    state['cursors'][wallet]=now
    return added,False


def summarize_group(g):
    assets=list(g['outcomes'].values())
    bought=[p for p in assets if p['buy_qty']>1e-7]
    buy=sum(p['buy_usd'] for p in assets)
    sell=sum(p['sell_usd'] for p in assets)
    net_qty=[p['buy_qty']-p['sell_qty'] for p in assets]
    if len(bought)>1:
        kind='MIXED'   # opposite outcomes purchased, not a standalone directional signal
    elif any(p['buy_qty'] and p['sell_qty'] for p in assets) or (buy and sell):
        kind='CHURN'   # in/out or rotating positions; NOT a simple BUY signal
    elif buy:
        kind='BUY'
    else:
        kind='SELL'
    return kind,buy,sell,net_qty


def format_alert(g,kind,holdings=None,meta=None,now=None):
    meta=meta or {}; now=now or int(time.time())
    markers={'BUY':'🟢 BUY / grouped fills','SELL':'🟠 SELL / grouped fills',
             'CHURN':'🟡 BOTH BUY + SELL — position changed',
             'MIXED':'🟣 BOTH OUTCOMES / possible hedge',
             'UNHELD':'⚫ BUY detected, but holding NOT found'}
    name=html.escape(g['name'] or meta.get('name') or g['wallet'][:12])
    lines=[f'<b>{markers[kind]}</b>',f'👤 {name}',
           '🏷 '+html.escape(g['title']),
           '🕓 Latest trade: '+dt.datetime.fromtimestamp(g['last'],dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')+
           f' (~{max(0,(now-g["last"])//60)}m ago)']
    for token,p in sorted(g['outcomes'].items(),key=lambda x:-(x[1]['buy_usd']+x[1]['sell_usd'])):
        line=(f'🎯 {html.escape(p["label"])}: {p["count"]} fills; '
              f'BUY ${p["buy_usd"]:,.2f} / SELL ${p["sell_usd"]:,.2f}; '
              f'net {p["buy_qty"]-p["sell_qty"]:+,.1f} shares')
        if p['buy_qty']:
            line+=f'; avg BUY ${p["buy_usd"]/p["buy_qty"]:.3f}'
        lines.append(line)
    if holdings is None:
        lines.append('📍 Current holdings: unverified (API unavailable)')
    elif holdings:
        desc=', '.join(f'{html.escape(g["outcomes"].get(token,{}).get("label") or token[:8])}: {qty:,.1f}'
                       for token,qty in holdings.items() if qty>=0.01)
        lines.append('📍 Public holdings snapshot: '+(desc or 'none >= 0.01 shares'))
        if sum(qty>=0.01 for qty in holdings.values())>1:
            lines.append('⚠️ Both sides currently held. Single BUY may be hedge/arbitrage.')
    else:
        lines.append('📍 Public holdings snapshot: no active position >= 0.01 shares')
    if kind in ('MIXED','CHURN'):
        lines.append('⚠️ NOT a standalone prediction; this account traded multiple sides or directions.')
    if kind=='UNHELD':
        lines.append('⚠️ Snapshot does not show the purchased outcome. Possibly sold, redeemed, resolved, or below dust threshold. Do not assume they still hold it.')
    lines.append(f'📊 Profitable closed trading positions: {num(meta.get("winrate")):.0%}; NOT prediction accuracy')
    factor=meta.get('profit_factor')
    if factor is not None:
        lines.append(f'📊 Closed-position profit factor: {factor:.2f} (not ROI)')
    observed=meta.get('observed_buy_fills',0)
    if observed>=10:
        expensive=meta.get('observed_expensive_buy_fills',0)
        lines.append(f'🔎 Observed expensive BUY fills: {expensive}/{observed} (partial monitored sample)')
    if meta.get('mechanical_events_observed'):
        lines.append('⚠️ Recent MERGE/SPLIT activity observed on this wallet; not proof this BUY was a prediction.')
    if g['url']:
        lines.append(f'<a href="{html.escape(g["url"],quote=True)}">Market</a> | '
                     f'<a href="https://polymarket.com/profile/{g["wallet"]}">Wallet</a>')
    lines.append('Research alert only; position can change after this snapshot.')
    return '\n'.join(lines)


def remember_suppressed_buy(state,g,now,reason,quote=None):
    """Record a skipped BUY for research, WITHOUT Telegram or a paper stake.

    Preserve its wallet/market tracking so later public SELLs can still be
    monitored. Bounded history lives in the existing state JSON (v31 schema).
    """
    ledger=state.setdefault('suppressed_v32',{'records':{},'counts':{}})
    records=ledger.setdefault('records',{})
    counts=ledger.setdefault('counts',{})
    key=f"{g['wallet']}|{g['condition']}|{g['last']}|{reason}"
    if key not in records:
        if MAX_SUPPRESSED_RECORDS>0:
            if len(records)>=MAX_SUPPRESSED_RECORDS:
                oldest=min(records,key=lambda k:records[k].get('observed_at',0))
                del records[oldest]
            purchased=[(token,p) for token,p in g['outcomes'].items() if p['buy_qty']>0]
            shares=sum(p['buy_qty'] for _,p in purchased)
            value=sum(p['buy_usd'] for _,p in purchased)
            records[key]={
                'wallet':g['wallet'],'condition':g['condition'],
                'source_ts':g['last'],'observed_at':now,
                'title':g.get('title',''),'url':g.get('url',''),
                'outcomes':[p.get('label','') for _,p in purchased],
                'outcome_tokens':[token for token,_ in purchased],
                'source_buy_usd':round(value,4),
                'source_buy_shares':round(shares,4),
                'source_avg_buy':round(value/shares,6) if shares else None,
                'reason':reason,
                'quote_best_ask':quote.get('best_ask') if quote else None,
                'quote_avg':quote.get('avg_price') if quote else None,
                'telegram_sent':False,
                'paper_stake_created':False,
            }
            counts[reason]=counts.get(reason,0)+1
    state.setdefault('tracking',{})[group_id(g['wallet'],g['condition'])]=now


def candidate_groups(state,now):
    ready=[]; removed=collections.Counter()
    for gid,g in list(state['pending'].items()):
        if now-g['last']>MAX_SIGNAL_AGE:
            removed['expired']+=1
            del state['pending'][gid]
            continue
        if now-g['last']<GROUP_SECONDS:
            continue
        kind,buy,sell,_=summarize_group(g)
        if kind=='BUY' and any(
            p['buy_qty']>0 and p['buy_usd']/p['buy_qty']>=MAX_BUY_PRICE
            for p in g['outcomes'].values()
        ):
            removed['high_buy_price']+=1
            remember_suppressed_buy(state,g,now,'source_avg_buy_at_or_above_limit')
            del state['pending'][gid]
            continue
        if kind=='BUY' and buy<MIN_BUY_USD:
            removed['small_buy']+=1;del state['pending'][gid];continue
        if kind=='SELL' and (sell<MIN_SELL_USD or gid not in state['tracking']):
            removed['untracked_or_small_sell']+=1;del state['pending'][gid];continue
        if kind in ('MIXED','CHURN') and max(buy,sell)<MIN_BUY_USD:
            removed['small_mixed']+=1;del state['pending'][gid];continue
        ready.append((gid,g,kind))
    # Round robin by wallet prevents 15 fills from one whale crowding out others.
    queues=collections.defaultdict(list)
    for row in ready:
        queues[row[1]['wallet']].append(row)
    for q in queues.values():
        q.sort(key=lambda x:(-x[1]['last'], -sum(p['buy_usd']+p['sell_usd'] for p in x[1]['outcomes'].values())))
    selected=[]
    wallets=sorted(queues,key=lambda w:-queues[w][0][1]['last'])
    for _ in range(MAX_PER_WALLET):
        for wallet in wallets:
            if len(selected)>=MAX_ALERTS:
                break
            if queues[wallet]: selected.append(queues[wallet].pop(0))
        if len(selected)>=MAX_ALERTS: break
    return selected,len(ready),dict(removed)


def dispatch(state,now):
    selected,ready,removed=candidate_groups(state,now)
    sent=0;failed=0;snaps={};market_skipped=0;market_errors=0
    for gid,g,kind in selected:
        wallet=g['wallet'];condition=g['condition']
        # Do not label old executions or non-tradable markets as actionable.
        if now-g['last']>MAX_SIGNAL_AGE:
            del state['pending'][gid]
            market_skipped += 1
            continue
        try:
            orderable = market_accepts_orders(condition)
        except Exception as exc:
            market_errors += 1
            print('MARKET WARNING:', condition[:20], str(exc)[:160])
            # Leave pending until the freshness limit expires; never guess.
            continue
        if not orderable:
            print('MARKET CLOSED/PAUSED: discarding stale alert', condition[:20])
            del state['pending'][gid]
            market_skipped += 1
            continue
        if wallet not in snaps:
            conditions={x[1]['condition'] for x in selected if x[1]['wallet']==wallet}
            try:
                snaps[wallet]=get_positions(wallet,conditions)
            except Exception as exc:
                print('POSITION WARNING:',wallet[:12],str(exc));snaps[wallet]=None
        snap=snaps[wallet]
        holdings=None if snap is None else snap.get(condition,{})
        # Never label a BUY as a clean forecast if it currently holds both outcomes.
        if kind=='BUY' and holdings is not None:
            positive=[token for token,p in g['outcomes'].items() if p['buy_qty']>0]
            if sum(v>=0.01 for v in holdings.values())>1 or (holdings and not any(holdings.get(t,0)>=0.01 for t in positive)):
                kind='MIXED'
            elif not any(holdings.get(t,0)>=0.01 for t in positive):
                kind='UNHELD'
        paper_candidate,paper_note=paper.prepare(state,g,kind,holdings,now)
        # Trader's fill is not today's available ask. Avoid a nearly $1 quote.
        if kind=='BUY' and paper_candidate is not None and (
            paper_candidate.get('best_ask',0)>=MAX_BUY_PRICE or
            paper_candidate.get('avg_price',0)>=MAX_BUY_PRICE
        ):
            print('PRICE FILTER: market ask too high',condition[:20])
            remember_suppressed_buy(state,g,now,'current_ask_at_or_above_limit',paper_candidate)
            del state['pending'][gid]
            market_skipped += 1
            continue
        # Opt-in: protect COPY BUYs, not informational SELL / MIXED updates.
        # Failed quality checks remain in a bounded, paper-free research log.
        if COPY_QUALITY_GATE and kind=='BUY':
            ok, reason, quality_facts=quality.assess(
                g,state.get('wallets',{}).get(wallet),holdings,paper_candidate)
            if not ok:
                print('COPY QUALITY: skipped BUY',condition[:20],reason)
                remember_suppressed_buy(state,g,now,'quality_'+reason,paper_candidate)
                del state['pending'][gid]
                market_skipped+=1
                continue
            # This record is saved only AFTER successful Telegram delivery.
            paper_candidate['quality_v32']=quality_facts
        message=format_alert(g,kind,holdings,state['wallets'].get(wallet),now)
        if paper_note:
            message+='\n'+html.escape(paper_note)
        try:
            v2.send_telegram(message)
        except Exception as exc:
            print('TELEGRAM ERROR:',type(exc).__name__,str(exc)[:100]);failed+=1
            continue  # retain unsent group in queue; retry
        paper.commit(state,paper_candidate)
        if kind in ('BUY','MIXED','CHURN'):
            state['tracking'][gid]=now
        elif kind=='SELL' and holdings is not None and not any(x>=0.01 for x in holdings.values()):
            state['tracking'].pop(gid,None)
        del state['pending'][gid]
        sent+=1
    return {'ready':ready,'sent':sent,'failed':failed,'queue':len(state['pending']),'removed':removed,'market_skipped':market_skipped,'market_errors':market_errors}


def prune(state,now):
    state['seen']={k:t for k,t in state['seen'].items() if t>=now-2*86400}
    state['tracking']={k:t for k,t in state['tracking'].items() if t>=now-21*86400}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state',default=STATE_FILE)
    parser.add_argument('--notify-status',action='store_true')
    args=parser.parse_args()
    now=int(time.time());state=read_state(args.state)
    v2.discover_candidates(state,now)
    discovery.discover_market_wallets(state,now)
    if not state['wallets']:
        raise RuntimeError('No wallet candidates. API discovery failed.')
    checked,score_errors=v2.score_candidates(state,now)
    eligible=v2.qualified_wallets(state,now)[:MONITOR_MAX]
    if FORENSIC_SAMPLE_ENABLED and eligible:
        forensics.sample_eligible(state,eligible,now)
    if not eligible:
        print('WARNING: No qualified wallets yet. Scoring will continue.')
    discovered=sum(1 for w in state['wallets'].values() if w.get('checked_at'))
    fills=0;baselines=0;watch_errors=0
    for wallet,_ in eligible:
        try:
            added,base=scan_wallet(state,wallet,now)
            fills+=added;baselines+=int(base)
        except Exception as exc:
            watch_errors+=1
            print('WATCH ERROR:',wallet[:12],str(exc))
    outcome=dispatch(state,now)
    paper_updates=paper.refresh_settlements(state,now)
    prune(state,now)
    v2.save_state(args.state,state)
    summary=(f'V3: candidates={len(state["wallets"])} scored={discovered} '
             f'watching={len(eligible)} new_fills={fills} baselines={baselines} '
             f'alerts={outcome["sent"]} ready={outcome["ready"]} queued={outcome["queue"]} '
             f'pruned={outcome["removed"]} scoring_errors={score_errors} '
             f'watch_errors={watch_errors} send_errors={outcome["failed"]} '
             f'market_skipped={outcome["market_skipped"]} market_errors={outcome["market_errors"]}')
    print(summary)
    print(paper.report(state,paper_updates))
    print(paper.wallet_digest(state))
    if args.notify_status:
        v2.send_telegram('<b>Tracker 3.2 — diagnostics</b>\n'+html.escape(summary)+
                         '\n'+html.escape(paper.report(state,paper_updates))+'\n'+html.escape(paper.wallet_digest(state))+'\nTrading wins are NOT prediction wins.')
    if eligible and watch_errors==len(eligible):
        raise RuntimeError('All wallet trade requests failed. Check /v2/trades connectivity.')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('FATAL:',type(exc).__name__,str(exc),file=sys.stderr)
        sys.exit(1)
