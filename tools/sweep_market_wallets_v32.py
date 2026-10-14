#!/usr/bin/env python3
"""Wide, bounded, public-data wallet discovery for the 15-hour research experiment.

No Telegram, trading, secrets, production state changes or persistent wallet
promotion. New addresses are CANDIDATES, not confirmed skillful traders.
A snapshot samples current market trades; it is not exhaustive chain history.
"""
import collections
import concurrent.futures
import json
import os
from pathlib import Path
import time

import smart_tracker_v2 as v2
import wallet_discovery_v32 as discovery

OUTPUT=Path(os.getenv('WIDE_REPORT_PATH','artifacts/wide-wallet-sweep.json'))
MAX_MARKETS=min(300,max(1,int(os.getenv('WIDE_MAX_MARKETS','180'))))
MAX_NEW_SCORES=min(100,max(0,int(os.getenv('WIDE_SCORE_SAMPLE','60'))))
PAGE_SIZE=50
MAX_GAMMA_PAGES=12


def find_markets(now):
    markets={}
    errors=0
    for offset in range(0,MAX_GAMMA_PAGES*PAGE_SIZE,PAGE_SIZE):
        try:
            batch=v2.request_json('/markets',{
                'active':'true','closed':'false',
                'limit':PAGE_SIZE,'offset':offset,
            },base=discovery.GAMMA,tries=1,timeout=9)
        except Exception as exc:
            errors+=1
            print('WIDE Gamma error:',type(exc).__name__,str(exc)[:85])
            continue
        if not isinstance(batch,list):
            errors+=1
            continue
        for market in batch:
            if not isinstance(market,dict):
                continue
            condition=str(market.get('conditionId') or '').lower()
            if not discovery.CONDITION_RE.fullmatch(condition):
                continue
            if market.get('closed') is True or market.get('active') is False:
                continue
            markets[condition]={
                'condition':condition,
                'volume24hr':v2.asfloat(market.get('volume24hr')),
            }
        if len(batch)<PAGE_SIZE:
            break
        if len(markets)>=MAX_MARKETS*2:
            break
    # Rotate among top recent-volume markets so repeat hourly runs vary the
    # sample while still favoring markets where wallet trades may exist.
    ranked=sorted(markets.values(),key=lambda m:(-m['volume24hr'],m['condition']))
    if len(ranked)>MAX_MARKETS:
        # Change the subset each hour, without pretending to cover all markets.
        shift=((now//3600)*MAX_MARKETS//2) % len(ranked)
        selected=[ranked[(shift+i)%len(ranked)] for i in range(MAX_MARKETS)]
    else:
        selected=ranked
    return selected,len(markets),errors


def inspect_market(condition,now):
    body=v2.request_json('/v2/trades',{
        'condition':condition,'limit':200,'taker_only':'false',
    },tries=1,timeout=9)
    if not isinstance(body,dict) or not isinstance(body.get('data'),list):
        raise ValueError('Unexpected public trades response')
    # The market-scoped call is a sampling window, NOT complete historical data.
    rows=body['data']
    return len(rows),discovery.extract_wallets(rows,condition,now)


def inspect_new_wallet(wallet):
    stats=v2.closed_stats(wallet)
    return {key:stats.get(key) for key in (
        'n','wins','winrate','recent_realized_pnl','gross_gains',
        'gross_losses','profit_factor','net_per_closed'
    )}


def main():
    now=int(time.time())
    source=json.loads(Path('state/smart_tracker_v3.json').read_text(encoding='utf-8'))
    known=source.get('wallets')
    if not isinstance(known,dict):
        raise ValueError('Tracker state snapshot lacks wallets')
    market_list,market_pool,gamma_errors=find_markets(now)
    seen_market_count=collections.Counter()
    all_addresses=set()
    trade_rows=0
    market_errors=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs={pool.submit(inspect_market,m['condition'],now):m['condition']
              for m in market_list}
        for future in concurrent.futures.as_completed(jobs):
            condition=jobs[future]
            try:
                count,wallets=future.result()
            except Exception as exc:
                market_errors+=1
                print('WIDE trade error:',condition[:14],
                      type(exc).__name__,str(exc)[:90])
                continue
            trade_rows+=count
            for wallet in wallets:
                all_addresses.add(wallet)
                seen_market_count[wallet]+=1
    new_candidates=sorted(all_addresses.difference(known))
    # Deterministic frequency-first candidate sample; a high trade frequency
    # is NOT evidence of forecasting ability. Do not select by PnL outcome.
    new_candidates.sort(key=lambda w:(-seen_market_count[w],w))
    to_score=new_candidates[:MAX_NEW_SCORES]
    score_errors=0
    samples=[]
    if to_score:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            jobs={pool.submit(inspect_new_wallet,w):w for w in to_score}
            for future in concurrent.futures.as_completed(jobs):
                wallet=jobs[future]
                try:
                    stats=future.result()
                except Exception as exc:
                    score_errors+=1
                    print('WIDE score error:',wallet[:12],type(exc).__name__,
                          str(exc)[:95])
                    continue
                stats['wallet']=wallet
                stats['sampled_markets']=seen_market_count[wallet]
                stats['passes_old_history_thresholds']=bool(
                    (stats['n'] or 0)>=v2.MIN_CLOSED and
                    (stats['winrate'] or 0)>=v2.MIN_WINRATE and
                    (stats['recent_realized_pnl'] or 0)>=v2.MIN_REALIZED_PNL)
                samples.append(stats)
    samples.sort(key=lambda x:(not x['passes_old_history_thresholds'],
                                -(x['n'] or 0),-x['sampled_markets'],x['wallet']))
    report={
        'schema':'wide_public_wallet_discovery_v32_1',
        'observed_at':now,
        'public_markets_in_listing':market_pool,
        'markets_selected':len(market_list),
        'markets_with_successful_trade_queries':len(market_list)-market_errors,
        'gamma_errors':gamma_errors,
        'trade_api_errors':market_errors,
        'trade_rows_sampled':trade_rows,
        'distinct_public_wallets':len(all_addresses),
        'already_known_wallets':len(all_addresses.intersection(known)),
        'new_candidate_wallets':len(new_candidates),
        # Preserve public candidate addresses in an isolated research artifact.
        # NEVER promote them automatically to the production watchlist.
        'new_candidate_addresses':new_candidates[:20000],
        'candidate_address_export_truncated':len(new_candidates)>20000,
        'new_wallets_selected_for_history_check':len(to_score),
        'new_wallet_history_api_errors':score_errors,
        'new_wallets_scored':len(samples),
        'new_wallets_passing_existing_history_thresholds':
            sum(int(m['passes_old_history_thresholds']) for m in samples),
        'scored_new_wallet_samples':samples,
        'limitations':[
            'Newly discovered addresses are not yet approved for Telegram or copying.',
            'Market trades are bounded 200-row snapshots, not exhaustive trade history.',
            'Old closed-position metrics are not independent proof of future returns.',
            'Only sampled new-wallet histories are evaluated; others remain unscored.',
            'No main/state file changes; next hourly run is an independent snapshot.',
            'No price execution, market settlement or real trade PnL is claimed.',
        ]
    }
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',
                      encoding='utf-8')
    print('WIDE SUMMARY: '+json.dumps({
        'markets_selected':report['markets_selected'],
        'trade_rows_sampled':trade_rows,
        'distinct_public_wallets':len(all_addresses),
        'new_candidate_wallets':len(new_candidates),
        'new_wallets_scored':len(samples),
        'new_wallets_passing_existing_history_thresholds':
            report['new_wallets_passing_existing_history_thresholds'],
        'market_errors':market_errors,'gamma_errors':gamma_errors,
        'score_errors':score_errors,
    },sort_keys=True))
    print('WIDE RESULT: PASS (new wallet addresses are unverified candidates)')


if __name__=='__main__':
    main()
