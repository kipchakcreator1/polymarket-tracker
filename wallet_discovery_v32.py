#!/usr/bin/env python3
"""Read-only discovery of wallets outside of Polymarket leaderboards.

Queries a bounded rotating sample of currently active market trade feeds.
Does not place orders, require private keys, or modify existing wallet records.
"""
import os
import re
import time

import smart_tracker_v2 as v2

GAMMA = 'https://gamma-api.polymarket.com'
CONDITION_RE = re.compile(r'^0x[0-9a-fA-F]{64}$')
INTERVAL = max(300, int(os.getenv('MARKET_DISCOVERY_INTERVAL', '3600')))
ADD_LIMIT = max(0, int(os.getenv('MARKET_DISCOVERY_ADD_LIMIT', '20')))
MAX_CANDIDATES = max(1250, int(os.getenv('MARKET_DISCOVERY_MAX_CANDIDATES', '2500')))
MAX_MARKETS_PER_PASS = 6
RECENT_SECONDS = 7 * 86400
PAGE_SIZE = 50
PAGE_OFFSETS = (0, 50, 100, 150, 200)


def extract_wallets(trades, condition, now):
    """Return recent, public proxy wallets from this exact market."""
    found = set()
    for row in trades:
        if not isinstance(row, dict):
            continue
        if str(row.get('condition_id', '')).lower() != condition.lower():
            continue
        timestamp = int(v2.asfloat(row.get('timestamp'), 0))
        if not (now - RECENT_SECONDS <= timestamp <= now + 60):
            continue
        address = str(row.get('proxy_wallet') or '').lower()
        if v2.WALLET_RE.fullmatch(address):
            found.add(address)
    return found


def discover_market_wallets(state, now=None):
    """Add limited new candidates; downstream scoring remains unchanged."""
    now = int(now if now is not None else time.time())
    meta = state.setdefault('market_discovery_v32', {})
    wallets = state.setdefault('wallets', {})
    if ADD_LIMIT == 0 or len(wallets) >= MAX_CANDIDATES:
        return {'added': 0, 'markets': 0, 'reason': 'limit'}
    if now - int(meta.get('last_success', 0)) < INTERVAL:
        return {'added': 0, 'markets': 0, 'reason': 'interval'}

    page_index = int(meta.get('page_index', 0)) % len(PAGE_OFFSETS)
    offset = PAGE_OFFSETS[page_index]
    try:
        markets = v2.request_json('/markets', {
            'active': 'true', 'closed': 'false', 'limit': PAGE_SIZE, 'offset': offset,
        }, base=GAMMA)
        if not isinstance(markets, list):
            raise ValueError('Unexpected Gamma /markets response')
    except Exception as exc:
        print('DISCOVERY V32: Gamma unavailable:', type(exc).__name__, str(exc)[:120])
        return {'added': 0, 'markets': 0, 'reason': 'gamma_error'}

    # Prioritize active markets within each page, while rotating pages every pass.
    markets = sorted((m for m in markets if isinstance(m, dict)),
                     key=lambda m: v2.asfloat(m.get('volume24hr'), 0), reverse=True)
    added = checked = errors = 0
    for market in markets:
        if checked >= MAX_MARKETS_PER_PASS or added >= ADD_LIMIT:
            break
        condition = str(market.get('conditionId') or '').lower()
        if not CONDITION_RE.fullmatch(condition):
            continue
        if market.get('closed') is True or market.get('active') is False:
            continue
        checked += 1
        try:
            response = v2.request_json('/v2/trades', {
                # Market-scoped v2/trades ignores start/end. Filter timestamps locally.
                'condition': condition, 'limit': 200, 'taker_only': 'false',
            })
            if not isinstance(response, dict) or not isinstance(response.get('data'), list):
                raise ValueError('Unexpected /v2/trades response')
            addresses = extract_wallets(response['data'], condition, now)
        except Exception as exc:
            errors += 1
            print('DISCOVERY V32: trade feed unavailable:',
                  condition[:16], type(exc).__name__, str(exc)[:100])
            continue
        for address in sorted(addresses):
            if address in wallets:
                continue
            wallets[address] = {'name': '', 'source': 'public_market_trades',
                                'first_discovered_at': now}
            added += 1
            if added >= ADD_LIMIT or len(wallets) >= MAX_CANDIDATES:
                break
        if len(wallets) >= MAX_CANDIDATES:
            break

    if checked > errors:
        meta['last_success'] = now
        meta['page_index'] = (page_index + 1) % len(PAGE_OFFSETS)
    print(f'DISCOVERY V32: page={offset} markets={checked} '
          f'new_wallets={added} errors={errors} total={len(wallets)}')
    return {'added': added, 'markets': checked, 'errors': errors}
