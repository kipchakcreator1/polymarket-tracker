#!/usr/bin/env python3
"""Conservative, optional COPY-signal gate for Tracker 3.2.

This does NOT estimate a contract's true probability or expected return.
A pass means that basic wallet, position, current-price and $1 paper
feasibility checks passed. It is not a recommendation to place an order.
All prices and closed-position metrics use *only* data known at signal time.
"""
import math
import os

MIN_PROFIT_FACTOR = float(os.getenv('COPY_MIN_PROFIT_FACTOR', '1.15'))
MAX_COPY_ABSOLUTE_RISE = float(os.getenv('COPY_MAX_ABSOLUTE_RISE', '0.05'))
MAX_COPY_RELATIVE_RISE = float(os.getenv('COPY_MAX_RELATIVE_RISE', '0.20'))
MAX_QUOTE_PRICE = float(os.getenv('MAX_BUY_PRICE', '0.98'))


def positive_number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def purchased_outcome(group):
    """A single BUY only; never treat multi-side or churn as a clean prediction."""
    purchased = [(token, pos) for token, pos in group['outcomes'].items()
                 if pos.get('buy_qty', 0) > 0]
    if len(purchased) != 1:
        return None
    token, pos = purchased[0]
    shares = positive_number(pos.get('buy_qty'))
    usd = positive_number(pos.get('buy_usd'))
    if shares is None or usd is None or pos.get('sell_qty', 0) > 0:
        return None
    px = usd / shares
    if not 0 < px < 1:
        return None
    return token, px


def assess(group, wallet_stats, holdings, quote):
    """Return (passes, reason, facts); missing data FAILS closed.

    Snapshot is only evidence of visible current holdings, not of past intent.
    The $1 market depth simulation is hypothetical, never a guaranteed fill.
    """
    outcome = purchased_outcome(group)
    if outcome is None:
        return False, 'not_single_directional_buy', {}
    token, source_price = outcome
    facts = {'source_price': round(source_price, 6)}
    stats = wallet_stats or {}
    factor = positive_number(stats.get('profit_factor'))
    facts['profit_factor'] = factor
    if factor is None:
        return False, 'wallet_gain_loss_ratio_unverified', facts
    if factor < MIN_PROFIT_FACTOR:
        return False, 'wallet_gain_loss_ratio_too_low', facts
    if holdings is None or not isinstance(holdings, dict):
        return False, 'holdings_unverified', facts
    if holdings.get(token, 0) < 0.01:
        return False, 'purchased_outcome_not_held', facts
    if any(qty >= 0.01 for other, qty in holdings.items() if other != token):
        return False, 'both_outcomes_held', facts
    if not isinstance(quote, dict):
        return False, 'current_ask_unavailable', facts
    best = positive_number(quote.get('best_ask'))
    avg = positive_number(quote.get('avg_price'))
    if best is None or avg is None or best >= 1 or avg >= 1:
        return False, 'current_ask_unavailable', facts
    facts.update(best_ask=best, quote_avg=avg,
                 copy_price_rise=round(avg-source_price, 6))
    if best >= MAX_QUOTE_PRICE or avg >= MAX_QUOTE_PRICE:
        return False, 'quote_at_or_above_price_limit', facts
    if avg - source_price > MAX_COPY_ABSOLUTE_RISE + 1e-9:
        return False, 'copy_price_moved_too_far', facts
    if avg / source_price - 1 > MAX_COPY_RELATIVE_RISE + 1e-9:
        return False, 'copy_price_relative_rise_too_large', facts
    # Paper's visible $1 sweep must not be confused with order eligibility.
    if quote.get('feasible_1usd') is not True:
        return False, 'one_usd_below_minimum_or_unknown', facts
    return True, 'passed_quality_checks_not_profit_guarantee', facts
