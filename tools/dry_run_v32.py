#!/usr/bin/env python3
"""Read-only preview of Tracker 3.2 on current public Polymarket data.

Never calls the tracker main(), dispatch(), Telegram, trading, save_state() or
paper.commit(). Never opens/writes the persisted state file. It exercises
discovery -> sample wallet history -> wallet trade polling -> grouped signals,
then samples market/order-book availability. All state is ephemeral memory.
"""
import sys
import time

import smart_tracker_v2 as v2
import smart_tracker_v3 as tracker
import wallet_discovery_v32 as discovery
import paper_tracker_v31 as paper

MAX_WALLETS = 3


def main():
    now = int(time.time())
    state = tracker.fresh_state()
    discovery.ADD_LIMIT = 8
    discovery.MAX_MARKETS_PER_PASS = 3
    result = discovery.discover_market_wallets(state, now)
    addresses = list(state['wallets'])[:MAX_WALLETS]
    print('DRY RUN discovered_new_wallets=', result.get('added', 0),
          'checked_markets=', result.get('markets', 0),
          'discovery_errors=', result.get('errors', 0))
    if not addresses:
        raise RuntimeError('No public wallets discovered: full dry run inconclusive.')

    scores_ok = polls_ok = trades_observed = qualified = 0
    for wallet in addresses:
        try:
            score = v2.closed_stats(wallet)
            scores_ok += 1
            state['wallets'][wallet].update(score)
            is_qualified = (
                score['n'] >= v2.MIN_CLOSED and
                score['winrate'] >= v2.MIN_WINRATE and
                score['recent_realized_pnl'] >= v2.MIN_REALIZED_PNL
            )
            qualified += int(is_qualified)
            print('DRY RUN wallet_history:',
                  wallet[:12], 'closed=', score['n'],
                  'profitable_fraction=', score['winrate'],
                  'qualified=', is_qualified)
        except Exception as exc:
            print('DRY RUN wallet_history ERROR', wallet[:12],
                  type(exc).__name__, str(exc)[:100])
        try:
            rows = tracker.get_wallet_trades(wallet, now - 300, now)
            polls_ok += 1
            trades_observed += len(rows)
            for trade in rows[:200]:
                tracker.add_fill(state, trade, now)
            print('DRY RUN trade_poll:', wallet[:12], 'recent_trades=', len(rows))
        except Exception as exc:
            print('DRY RUN trade_poll ERROR:', wallet[:12],
                  type(exc).__name__, str(exc)[:100])
    print('DRY RUN sampling: wallet_histories_ok=', scores_ok,
          'trade_polls_ok=', polls_ok, 'recent_trades=', trades_observed,
          'qualified_in_small_sample=', qualified)
    if scores_ok == 0:
        raise RuntimeError('All wallet history queries failed')
    if polls_ok == 0:
        raise RuntimeError('All wallet trade polling queries failed')

    # Deliberately never call dispatch(): it normally sends Telegram and
    # commits virtual $1 paper positions. Filtering happens ONLY in memory.
    selected, ready, removed = tracker.candidate_groups(state, now)
    print('DRY RUN grouped_signals=', len(state['pending']),
          'eligible_alert_groups=', ready, 'selected=', len(selected),
          'suppressed_or_skipped=', dict(removed))
    print('DRY RUN no_telegram=true no_paper_commit=true '
          'no_state_file_changes=true no_real_trades=true')

    # Check current public orderbook independently from signal qualification.
    markets = v2.request_json('/markets', {
        'active': 'true', 'closed': 'false', 'limit': 10, 'offset': 0,
    }, base=discovery.GAMMA, tries=2, timeout=12)
    if not isinstance(markets, list):
        raise RuntimeError('Gamma market metadata unavailable')
    probe_status = 'no_valid_market'
    for market in markets:
        condition = str(market.get('conditionId') or '')
        if not discovery.CONDITION_RE.fullmatch(condition):
            continue
        try:
            accepts = tracker.market_accepts_orders(condition)
            probe_status = 'market_open' if accepts else 'market_not_accepting_orders'
            if not accepts:
                continue
            body = v2.request_json('/v2/trades', {
                'condition': condition, 'limit': 20, 'taker_only': 'false',
            }, tries=2, timeout=12)
            if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                raise RuntimeError('invalid market trades body')
            token = next((str(row.get('token_id'))
                          for row in body['data'] if row.get('token_id')), None)
            if token:
                quote = paper.book_simulation(token)
                probe_status = ('book_1usd_quote' if quote is not None
                                else 'book_no_1usd_ask_depth')
                if quote:
                    print('DRY RUN orderbook: best_ask=', quote['best_ask'],
                          'avg_price=', quote['price'],
                          'one_dollar_meets_min=', quote['one_dollar_meets_min'])
            break
        except Exception as exc:
            print('DRY RUN market/book WARNING:', type(exc).__name__, str(exc)[:120])
    print('DRY RUN market_probe=', probe_status)
    print('DRY RUN RESULT=PASS read-only integration paths checked; '
          'a sample with zero qualified wallets or signals is not proof of profitability')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('DRY RUN RESULT=FAIL:', type(exc).__name__, str(exc)[:300],
              file=sys.stderr)
        sys.exit(1)
