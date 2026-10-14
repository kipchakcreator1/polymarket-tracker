#!/usr/bin/env python3
"""Public read-only probe of the /v2/activity MERGE/SPLIT/REDEEM endpoint.

No secrets, signals, Telegram, wallet addresses in logs, or state file changes.
"""
import sys
import time
import smart_tracker_v2 as v2
import wallet_discovery_v32 as discovery
import wallet_forensics_v32 as fx


def main():
    now=int(time.time())
    markets=v2.request_json('/markets',
        {'active':'true','closed':'false','limit':20,'offset':0},
        base=discovery.GAMMA,tries=2,timeout=12)
    if not isinstance(markets,list):
        raise RuntimeError('Gamma shape unavailable')
    wallets=set()
    for m in markets[:5]:
        condition=str(m.get('conditionId') or '')
        if not discovery.CONDITION_RE.fullmatch(condition):
            continue
        response=v2.request_json('/v2/trades',
            {'condition':condition,'limit':50,'taker_only':'false'},
            tries=2,timeout=12)
        if not isinstance(response,dict) or not isinstance(response.get('data'),list):
            raise RuntimeError('Unexpected public trade feed')
        wallets.update(discovery.extract_wallets(response['data'],condition,now))
        if len(wallets)>=5:
            break
    if not wallets:
        raise RuntimeError('No public activity test wallet available')
    errors=[]
    for wallet in sorted(wallets)[:5]:
        try:
            profile=fx.activity_profile(wallet,now)
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {str(exc)[:90]}')
            continue
        print('FORENSIC LIVE SMOKE: API response valid;',
              'merge_rows=',profile['merge_rows'],
              'split_rows=',profile['split_rows'],
              'redeem_rows=',profile['redeem_rows'],
              'complete=',profile['activity_complete'])
        print('FORENSIC LIVE SMOKE PASS: activity types are observation only')
        return
    raise RuntimeError('Activity API unavailable in this sample: ' + '; '.join(errors[:3]))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('FORENSIC LIVE SMOKE FAIL:',type(exc).__name__,str(exc)[:300],
              file=sys.stderr)
        sys.exit(1)
