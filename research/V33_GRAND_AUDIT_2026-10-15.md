# v3.3 grand audit — 2026-10-15 (California) / 2026-10-16 UTC

## Scope and evidence
- GitHub Actions production runs, v3.3 read-only bot and offline tests.
- Snapshot of research state at 2026-10-16 01:41 UTC: 10,000 discovered, 250 monitored, 139 lifetime verified BUY sends and 2 quiet-status notices.
- Last recorded BUY sent at 2026-10-16 00:01:59 UTC (17:01:59 California on Oct 15). Active polls after that still found trades; bot was not wholly offline.
- Lifetime veto counters at snapshot (not a unique-market or predictive performance dataset): `small_trade=4779`, `mixed_or_sell=1719`, `source_price_excluded=372`, `recent_positive_rate_low=155`, `recent_unredeemed_loss_cluster=158`, `recent_form_request_error=354`, `conflicting_wallets=81`, `political_market=186`, `price_moved=66`.
- Offline audit validation: 63 unit tests passed at GitHub Actions run 38836592370.

## Diagnosed root causes
1. **Over-filtering, not a permanently dead Telegram route**: 250 wallets repeatedly return fresh transactions yet candidate vetoes reduce confirmed, tradable BUY alerts to near zero for long periods.
2. **Silent re-buy black hole**: state `sent[wallet|condition|token]` blocked a wallet's later fresh BUYs on the same market for the entire seven-day retention period. Independent fill-level dedupe exists but was never consulted to allow new long-market buys.
3. **Stale time-of-check on fast 5-minute markets**: age was computed at the start of the ~250-wallet sweep; orderbook and performance verification can consume more time before Telegram send.
4. **Misinterpreted public positions**: `REDEEMABLE_LOST` can include a non-losing record that must be ignored (not invalidate the entire wallet), and `last_event_at` is position *activity*, not the actual market settlement timestamp. A hard “three losses today” interpretation was unsupported.
5. **Conflicts quietly discarded**: opposing qualifying wallet BUY directions were vetoed without making the ambiguity transparent to Telegram users.
6. **GitHub schedule not real-time**: GitHub cron can be delayed; a ~50m polling session reduces gaps but cannot guarantee 1-minute 24/7 scheduling. Default concurrency replaces *pending* runs; that is different from canceling an active monitoring job.
7. **No realized copied-trade proof**: `profitable_closed_rate` is old wallet outcomes, not forecasting accuracy; a $5 orderbook snapshot is not guaranteed fill or profit. The project does NOT backtest alerts against a realized hypothetical $5 exit model.

## Changes implemented
- Monitor 250 qualified wallets; source BUY minimum moved **$50 → $35** (the Telegram message explicitly notes sub-$50 volume). Keep checks for price 15–85¢, max rise/drop, market acceptance, both sides of the book, slippage, minimum order, adverse active PnL and material losses. No orders are placed.
- Limit silent duplicate suppression to **30 minutes** for a distinct NEW buy on the same wallet/market/outcome; retain transaction-ID-level deduplication and short catch-up window.
- Recheck 5m market age/end time directly before sending Telegram; never present a now-expired rapid trade as timely.
- Raise the material unredeemed-loss-activity veto from 2 to 5 (6+ still blocks), keep recent positive-rate/positive net-PnL/profit-factor and loss-streak protections; relabel the activity proxy accurately. 55% of recent 20 positions is still not proof of profitable copying.
- Send separately labeled **⚠️ КОНФЛИКТ** notices only when two selected wallets have new qualifying opposite BUY directions on a nonpolitical market. These warnings explicitly state that copyability/book liquidity is NOT verified, and are deduplicated by market.
- Per-poll reason deltas (`last_run.this_poll`) added to support numerical, run-by-run monitoring rather than broad lifetime totals.
- Keep a truthful no-signal status at most once an hour, separately from verified BUY alerts.
- Preserve old 3.1 and 3.2 workflows as manually runnable but cron-paused; keep the distinct v3.3 token, state and concurrency group.

## Tests and acceptance
- Offline tests cover second BUY at +29m vs +31m, near-end 5m delayed verification, adverse recent PnL, corrupted response, spoofed positions, Telegram failure, conflict alerts, politics exclusion, and dedupe.
- End-to-end Polymarket and Telegram delivery requires real scheduled workflow observation. Green offline tests prove logic correctness only for covered cases, **not** that a new profitable BUY will arrive on command.
- Verification: https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38836592370
- Live workflow: https://github.com/kipchakcreator1/polymarket-tracker/actions/workflows/v33-telegram.yml

## Next quantifiable goals (without paying for infrastructure)
1. Check first post-deploy run: test results, latest poll timestamp, `this_poll` rejection reasons, confirmed alert/quiet/conflict counters.
2. Compare verified BUY/h over a full day, median and 95th-percentile signal delay, API error/h, and watchlist churn.
3. If useful, add paper follow-up for **post-alert** market outcomes and executable bid/ask costs before claiming that any wallet is profitable to copy.

**Zero-money constraint:** no VPS, paid data, premium GitHub runner or real-money orders. Public API and GitHub-hosted standard runner only. Delays/limits are possible.
