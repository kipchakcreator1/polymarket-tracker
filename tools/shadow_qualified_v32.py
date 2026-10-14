#!/usr/bin/env python3
"""One-time READ-ONLY shadow observation of already-qualified wallets.

NEVER calls dispatch, send_telegram, paper.commit, main, or save_state.
Reads a committed state snapshot (not the current production runtime); writes
only a separate JSON report in the Actions working directory for artifact upload.
Missing or partial API data is marked unknown, never a successful signal.
"""
import collections
import concurrent.futures
import json
import os
from pathlib import Path
import time

import smart_tracker_v2 as v2
import smart_tracker_v3 as tracker
import paper_tracker_v31 as paper
import copy_quality_v32 as quality

OUTPUT=Path(os.getenv('SHADOW_REPORT_PATH','artifacts/shadow-observation.json'))
STATE=Path('state/smart_tracker_v3.json')
MAX_WALLETS=min(40,max(1,int(os.getenv('SHADOW_WALLET_SAMPLE','24'))))
MAX_QUOTE_PROBES=12


def sample_ranked(eligible, n):
    """Deterministic rank-stratified sample, NOT random or representative."""
    if len(eligible)<=n:
        return eligible
    return [eligible[min(len(eligible)-1, int((i+.5)*len(eligible)/n))]
            for i in range(n)]


def fetch_recent(wallet, start, end):
    """Bounded cursor walk, checked for complete results and wallet ownership."""
    cursor=None
    out=[]
    for _ in range(3):
        args={'user':wallet,'start':start,'end':end,
              'limit':200,'taker_only':'false'}
        if cursor:
            args['cursor']=cursor
        body=v2.request_json('/v2/trades',args,tries=2,timeout=12)
        if not isinstance(body,dict) or not isinstance(body.get('data'),list):
            raise ValueError('Unexpected wallet trade response')
        pg=body.get('pagination')
        if not isinstance(pg,dict):
            raise ValueError('Missing trade pagination')
        for trade in body['data']:
            if not isinstance(trade,dict):
                continue
            if str(trade.get('proxy_wallet') or '').lower()!=wallet:
                raise ValueError('Trade wallet mismatch')
            if trade.get('side') in ('BUY','SELL'):
                out.append(trade)
        if not pg.get('has_more'):
            return out
        cursor=pg.get('next_cursor')
        if not cursor:
            raise ValueError('Incomplete trade cursor')
    raise RuntimeError('Trade page limit reached; do not count incomplete history')


def read_existing_state():
    raw=json.loads(STATE.read_text(encoding='utf-8'))
    if raw.get('version')!=tracker.VERSION:
        raise RuntimeError('State snapshot version incompatible')
    if not isinstance(raw.get('wallets'),dict):
        raise RuntimeError('State snapshot missing wallets')
    return raw


def main():
    now=int(time.time())
    source=read_existing_state()
    eligible=v2.qualified_wallets(source,now)
    sampled=sample_ranked(eligible,MAX_WALLETS)
    counters=collections.Counter()
    reasons=collections.Counter()
    observations=[]
    shadow=tracker.fresh_state()  # in-memory, never persisted
    shadow['wallets']={addr:dict(meta) for addr,meta in sampled}
    freshness_seconds=5*60
    start=now-15*60
    print(f'SHADOW: qualified_from_snapshot={len(eligible)} sampled={len(sampled)} '
          f'window_minutes=15 actionable_fresh_minutes=5')
    if not sampled:
        report={'status':'inconclusive_no_fresh_qualified_snapshot',
                'observed_at':now,'qualified':0,'sampled':0,
                'counts':{},'reasons':{},'observations':[]}
        OUTPUT.parent.mkdir(parents=True,exist_ok=True)
        OUTPUT.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print('SHADOW RESULT: INCONCLUSIVE no eligible snapshot wallets')
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(fetch_recent,addr,start,now):addr for addr,_ in sampled}
        for future in concurrent.futures.as_completed(futures):
            addr=futures[future]
            try:
                rows=future.result()
            except Exception as exc:
                counters['trade_api_errors']+=1
                print('SHADOW trade error:',addr[:12],type(exc).__name__,str(exc)[:90])
                continue
            counters['wallets_polled']+=1
            counters['trade_rows_15m']+=len(rows)
            for t in rows:
                stamp=int(v2.asfloat(t.get('timestamp'),0))
                if stamp>now+60 or stamp<start:
                    continue
                if stamp<now-freshness_seconds:
                    counters['old_trades_excluded']+=1
                    continue
                if tracker.add_fill(shadow,t,now):
                    counters['new_fresh_trade_rows']+=1
    # Test gating on ALL grouped, not just the live Telegram queue limit.
    tracker.MAX_ALERTS=1000
    tracker.MAX_PER_WALLET=1000
    selected,ready,discarded=tracker.candidate_groups(shadow,now)
    counters['grouped_ready']=ready
    counters['grouped_selected']=len(selected)
    for reason,amount in discarded.items():
        reasons['pre_'+reason]+=amount
    counters['unmatured_group_count']=len(shadow['pending'])-len(selected)
    for gid,g,kind in selected:
        if kind!='BUY':
            reasons['not_clean_buy_'+kind.lower()]+=1
            continue
        if counters['quote_probes']>=MAX_QUOTE_PROBES:
            reasons['quote_probe_cap']+=1
            continue
        counters['quote_probes']+=1
        row={'wallet':g['wallet'],'condition':g['condition'],
             'source_ts':g['last'],'observed_at':now,
             'kind':kind,'title':g.get('title','')[:140],
             'token_ids':[token for token,p in g['outcomes'].items() if p.get('buy_qty',0)>0]}
        try:
            if not tracker.market_accepts_orders(g['condition']):
                row.update(status='blocked',reason='market_closed_or_paused')
            else:
                wallet=g['wallet']
                snap=tracker.get_positions(wallet,{g['condition']})
                holdings=snap.get(g['condition'],{})
                # A historical position snapshot cannot prove the intent of the trade.
                quote,note=paper.prepare(shadow,g,'BUY',holdings,now)
                if quote is None:
                    row.update(status='blocked',reason='paper_quote_or_holdings_unavailable',
                               note=note or '')
                else:
                    # Closed-position gain/loss ratio as of NOW, not a future score.
                    stats=v2.closed_stats(wallet)
                    row['recent_closed_positions']=stats['n']
                    row['recent_profit_factor']=stats.get('profit_factor')
                    passed,why,facts=quality.assess(g,stats,holdings,quote)
                    row.update(status='passed_basic_checks' if passed else 'blocked',
                               reason=why,source_avg_buy=facts.get('source_price'),
                               current_avg_ask=facts.get('quote_avg'),
                               best_ask=facts.get('best_ask'),
                               price_rise=facts.get('copy_price_rise'),
                               one_usd_feasible=quote.get('feasible_1usd'))
                    counters['qualified_basic_passes']+=int(passed)
        except Exception as exc:
            row.update(status='unknown_api_error',
                       reason=type(exc).__name__+': '+str(exc)[:90])
            counters['quote_or_history_api_errors']+=1
        observations.append(row)
        reasons[row['reason']]+=1
    report={
        'schema':'polymarket_shadow_v32_1',
        'status':'read_only_single_snapshot',
        'observed_at':now,
        'source_snapshot':'test_branch_state_copy_read_only',
        'qualified_from_snapshot':len(eligible),
        'sampled_rank_stratified':len(sampled),
        'counts':dict(counters),
        'reasons':dict(reasons),
        'observations':observations,
        'limitations':[
            'No trades, Telegram, virtual portfolio commits, or production state writes',
            'Single observation: no measured future outcome, profit or signal quality',
            'Snapshot wallet scores may be stale; sample is rank-stratified, not representative',
            'A passing basic gate does not imply a fair-value edge or actual executability',
            'Only complete, recent, API-visible trades count; incomplete pages are errors',
            'Paper $1 depth is hypothetical; it can fail exchange order minimums',
        ],
    }
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('SHADOW SUMMARY: '+json.dumps(
        {'qualified':len(eligible),'sampled':len(sampled),
         'counts':dict(counters),'reasons':dict(reasons)},
        ensure_ascii=False,sort_keys=True))
    print('SHADOW RESULT: PASS (read-only sample; NOT proof of profitability)')


if __name__=='__main__':
    main()
