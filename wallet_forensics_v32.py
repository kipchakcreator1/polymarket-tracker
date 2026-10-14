#!/usr/bin/env python3
"""Read-only sampling of account operations excluded from BUY/SELL trade feeds.

MERGE/SPLIT can suggest inventory or hedging behavior; REDEEM alone is
normal settlement and does not prove any strategy or PnL. Only complete,
bounded scans produce a label; errors do not overwrite existing metadata.
"""
import os
import time

import smart_tracker_v2 as v2

LOOKBACK_DAYS = 14
MAX_PAGES = 3
PAGE_SIZE = 100
MAX_PER_PASS = int(os.getenv('FORENSIC_WALLETS_PER_PASS', '3'))
REFRESH = 24 * 3600


def activity_profile(wallet, now):
    if not v2.WALLET_RE.fullmatch(wallet):
        raise ValueError('Invalid wallet')
    counts = {'MERGE': 0, 'SPLIT': 0, 'REDEEM': 0}
    cursor = None
    for _ in range(MAX_PAGES):
        params = {'user': wallet, 'start': now - LOOKBACK_DAYS*86400,
                  'end': now, 'limit': PAGE_SIZE, 'type': 'SPLIT,MERGE,REDEEM'}
        if cursor:
            params['cursor'] = cursor
        body = v2.request_json('/v2/activity', params)
        if (not isinstance(body, dict) or not isinstance(body.get('data'), list)
                or not isinstance(body.get('pagination'), dict)):
            raise ValueError('Unexpected activity response')
        for row in body['data']:
            if not isinstance(row, dict):
                continue
            owner = str(row.get('proxy_wallet') or '').lower()
            if owner and owner != wallet:
                raise ValueError('Activity wallet mismatch')
            event = str(row.get('type') or '').upper()
            if event in counts:
                counts[event] += 1
        pagination = body['pagination']
        if not pagination.get('has_more'):
            return {
                'activity_checked_at': now,
                'window_days': LOOKBACK_DAYS,
                'merge_rows': counts['MERGE'],
                'split_rows': counts['SPLIT'],
                'redeem_rows': counts['REDEEM'],
                'mechanical_events_observed': counts['MERGE'] + counts['SPLIT'] > 0,
                'activity_complete': True,
            }
        cursor = pagination.get('next_cursor')
        if not cursor:
            raise ValueError('Activity has_more but missing cursor')
    raise RuntimeError('Activity page cap hit; no classification from partial history')


def sample_eligible(state, eligible, now=None):
    now = int(now if now is not None else time.time())
    checked = errors = 0
    for wallet, meta in eligible:
        if checked >= MAX_PER_PASS:
            break
        if now - meta.get('activity_checked_at', 0) < REFRESH:
            continue
        checked += 1
        try:
            result = activity_profile(wallet, now)
        except Exception as exc:
            errors += 1
            print('FORENSICS WARNING:', wallet[:12],
                  type(exc).__name__, str(exc)[:130])
            continue
        state['wallets'][wallet].update(result)
    print(f'FORENSICS: checked={checked} errors={errors}')
    return {'checked': checked, 'errors': errors}
