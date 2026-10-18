# v3.3 risk-gate audit — first controlled repairs

**Observation window:** 2026-10-17/18 UTC. **Scope:** read-only research branch `audit/v33-risk-gate-20261017`. No real orders, Telegram deliveries, secret reads, or changes to the production v3.3 monitoring branch/workflows.

## Confirmed defects and changes

1. **An API uncertainty was causing irrevocable candidate deletion.** Before the audit, `deliver()` removed the pending BUY even when the public risk API could not complete; the 10-minute error cache usually outlived the 5-minute BUY freshness rule. The audit branch keeps only fresh, unverified candidates pending and retries transient network/429/5xx failures after a shorter bounded cooldown. No unchecked BUY is sent.
2. **BUY delivery lacked a hard message receipt assertion.** `send_telegram()` normally returns a positive `message_id`, but an invalid or mocked return could still mark a BUY delivered. The audit branch requires a strictly positive integer, otherwise retains pending for retry.
3. **Live v2 positions expose `last_event_at = 0` on settled losers.** The former parser treated this as fatal, discarding the whole wallet check. Now a settled loss with zero activity timestamp is preserved as `_timestamp_unknown=True`, outside timed 24h/90d comparisons. It triggers `recent_undated_loss_history` and is **not** eligible for BUY. No inferred settlement date or positive score is manufactured. Malformed CLOSED timestamps and nonzero impossible dates still fail closed.
4. The audit reports API/schema failures and invalid timestamp samples separately from a failing economic strategy. A sampled v2 position page is never marked a complete economic ledger.

## Reproducible live read-only API evidence

- [First bounded diagnostic run](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39102937198): 60 rows each of CLOSED and REDEEMABLE_LOST for four wallets; the latter showed 60, 60, 60, 59 observed negative PnL rows. All had `pagination.has_more=true` on this first sample — not sufficient for totals.
- [Expanded one-page diagnostic](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39103045072) with `limit=250`:
  - Wallet `0x186f64ad1d9d…`: legacy sample 91 CLOSED, 93.41% positive, PF 86.84. v2 CLOSED 92 rows (complete page), REDEEMABLE_LOST 88 (complete page), 87 negative, 10 with <= −$100; **one raw `last_event_at=0`**.
  - Wallet `0x0a7491c71efc…`: legacy sample 83 CLOSED, 97.59% positive, PF 326.68. v2 CLOSED 84 (complete page), REDEEMABLE_LOST 87 (complete page), 86 negative, 5 with <= −$100; **one raw `last_event_at=0`**.
  - Wallet `0xf227388c01e3…`: v2 CLOSED 250 and REDEEMABLE_LOST 250, **both still paginating**; all 250 sampled redeemables had negative PnL.
  - Wallet `0x5b6331e7ff08…`: v2 CLOSED 250 and REDEEMABLE_LOST 250, **both still paginating**; 247 sampled redeemables negative, 28 <= −$100.
- Position rows are not independent sports-event bets; v2 CLOSED and REDEEMABLE may have overlap or distinct timing and may include hedges, partial sales, maker activity, other categories, and market-resolution accounting. Consequently **these observations do NOT establish a corrected win rate, wallet net profit, or follower ROI**. One completed status pagination is not a reconciled cash-flow ledger. No observed score automatically promotes or permanently blacklists a wallet.

## Regression evidence

[Offline regression workflow](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39103267613) passed **108 v3.3 BUY/risk tests, 8 OPEN tests, 5 bounded v2 audit tests** = **121 tests**. Unit tests use mocks, while the diagnostic step performs narrowly bounded public read-only API calls. Passing regression tests does not establish live signal profitability, coverage, or correctness of every historical position.

## Cross-check against existing research-only candidate ranking

The branch also already contains `research/v33_evidence_rank.json` (source updated at 2026-10-18 UTC). It lists **169 wallets audited**, **0 historical-audit-ready**, **0 forward-paper-evaluable**, **0 live-approved**. These are report-status counts, not proof that no profitable sports signal exists. It means the present evidence does not support automatically replacing the wallet shortlist with an apparently profitable copy-trader ranking. The separate `research/v33_wallet_copy_cohort.json` explicitly labels its ROI estimates price proxies, not executable net follower returns, and likewise approves 0 wallets for live BUY.

## Still unresolved / next gate

- The old universe shortlist still uses up to 100 **legacy closed positions** to rank and admit 300 wallets. It is not a complete economic or predictive ranking. Keep this behavior unchanged in production pending a real, event-level and cash-flow-reconciled replacement.
- v2 REDEEMABLE_LOST alone does not substitute for wallet trade/activity ledger, ownership reconciliation, independent event outcomes, and follower execution replay.
- Validate timestamp-zero cases against official resolution history before assigning them to any time-window (24h or 90d).
- Build the next research-only **shadow shortlist** with explicit COMPLETE/UNKNOWN state, no promotion on incomplete history, and compare same-cohort forward paper results against the active bot. Existing audit changes must not be merged into live until monitoring, retry overhead, and Telegram ack behavior are verified with canary tests.
