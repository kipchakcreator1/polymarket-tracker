#!/usr/bin/env python3
"""Read-only live smoke check: Polymarket Gamma and Data API discovery.

No secrets, trading, Telegram, or state files. Sample only; no profit inference.
"""
import datetime as dt
import sys
import time
import smart_tracker_v2 as v2
import wallet_discovery_v32 as discovery


def main():
    now=int(time.time())
    rows=v2.request_json('/markets',{
        'active':'true','closed':'false','limit':50,'offset':0
    },base='https://gamma-api.polymarket.com',tries=2,timeout=12)
    if not isinstance(rows,list):
        raise RuntimeError('Gamma response is not list')
    markets=[m for m in rows if isinstance(m,dict) and
             discovery.CONDITION_RE.fullmatch(str(m.get('conditionId') or ''))]
    markets=sorted(markets,key=lambda m:v2.asfloat(m.get('volume24hr'),0),reverse=True)
    print(f'LIVE SMOKE gamma_rows={len(rows)} valid_market_ids={len(markets)}')
    if not markets:
        raise RuntimeError('No valid Gamma market IDs; cannot verify market discovery')
    total_rows=0
    recent_wallets=set()
    checked=0
    for m in markets[:5]:
        condition=m['conditionId']
        data=v2.request_json('/v2/trades',{
            'condition':condition,'limit':50,'taker_only':'false'
        },tries=2,timeout=12)
        if not isinstance(data,dict) or not isinstance(data.get('data'),list):
            raise RuntimeError('Unexpected v2/trades response')
        checked+=1
        trades=data['data']
        mismatch=[r for r in trades if isinstance(r,dict) and
                  str(r.get('condition_id','')).lower()!=condition.lower()]
        if mismatch:
            raise RuntimeError('Condition-filtered trades includes wrong market rows')
        total_rows+=len(trades)
        recent_wallets.update(discovery.extract_wallets(trades,condition,now))
        print(f'LIVE SMOKE market={checked} trade_rows={len(trades)}')
    print(f'LIVE SMOKE result: sampled_markets={checked} '
          f'trade_rows={total_rows} unique_recent_wallets={len(recent_wallets)}')
    if not total_rows:
        print('INCONCLUSIVE: market sample returned no trades. '
              'API responds but wallet discovery yield not verified.')
        return 0
    if not recent_wallets:
        print('INCONCLUSIVE: trade rows seen but none met recent-valid-wallet filter')
        return 0
    state={'wallets':{}}
    # Exercise actual module with bounded single pass; no persisted file.
    original_limit=discovery.ADD_LIMIT
    original_markets=discovery.MAX_MARKETS_PER_PASS
    try:
        discovery.ADD_LIMIT=5
        discovery.MAX_MARKETS_PER_PASS=3
        result=discovery.discover_market_wallets(state,now)
    finally:
        discovery.ADD_LIMIT=original_limit
        discovery.MAX_MARKETS_PER_PASS=original_markets
    print('LIVE SMOKE actual discovery:',
          f"added={result.get('added',0)} checked={result.get('markets',0)}",
          f"errors={result.get('errors',0)}")
    if result.get('added',0)==0 and result.get('errors',0)==result.get('markets',0):
        raise RuntimeError('Actual market discovery could not read sampled trade feeds')
    return 0


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('LIVE SMOKE FAILURE:',type(exc).__name__,str(exc)[:300],file=sys.stderr)
        sys.exit(1)
