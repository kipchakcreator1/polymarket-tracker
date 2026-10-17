# Forward sports BUY net paper PnL: Critical evidence-loss fix (2026-10-17)

## Root cause

Before this fix, `v33_signal_bot.py::main()` took `now=int(time.time())`
**before** requests to Polymarket, snapshot book checks, recent forms, and
Telegram delivery. `deliver(clock=time.time)` stamped paper entry events
with `alert_at` / `quoted_at` that could be a few seconds **AFTER**
that earlier `now`. The final checkpoint cleanup required
`0 <= now - alert_at <= 45 * 86400` (similarly for SHADOW's `quoted_at`).
As a result, **every quote produced during the same polling pass was
silently discarded** before the GitHub state file was saved. Observed
state: 242 cumulative Telegram API accepted BUYs / 95 sports accepts and
zero `signal_evidence` and `shadow_evidence` entries. These aggregate
counters predate the fix; **never infer that 242 historical prices exist**.
Cannot backfill actual historic order book snapshots from counter data.

Repair commit `37e97c11e5406254eecd24cb5c286d674077cea3` adds
`keep_forward_evidence()` allowing a bounded +/-15m current-poll skew
while still pruning old entries and max 900 SHADOW samples. Regression
tests verify `alert_at=now+7s`, `quoted_at=now+12s` survive, whereas
timestamps >15m ahead and entries older than 45 days do not. No order
execution logic or BTC restrictions changed.

Quote metadata now explicitly carries:
- `hypothetical_copy_ask`: VWAP from actual visible order book ask sweep.
- `hypothetical_copy_shares`: size visible at that $5 sweep.
- `hypothetical_copy_stake_usd`: $5 reference only.
- `book_quoted_at`: verification-start timestamp bound; **not a trade fill**.
- `best_ask`, `spread`, `source_trade_at`, `topic` and risk-veto outcome.

## Fee-adjusted independent settlement study

Added `tools/v33_forward_net_paper.py` with tests and
`research/v33_forward_net_paper.json` hourly read-only workflow
`.github/workflows/v33-forward-net-paper.yml`. It performs a strict
exact-token join between forward quote key and independently audited
Data API v2 resolution payouts. A quote with missing share-depth,
a stale/unverified official result, inconsistent VWAP or settlement before
quote **cannot** count as settled paper PnL.

Modelled Polymarket taker buy fees use **shares** taken from the BUY:
`fee_usdc_equiv = shares * rate * price * (1-price)`,
`shares_fee = fee_usdc_equiv / price`. A winning hold pays out the
remaining shares; a losing hold pays $0. Model fee coefficients:
0 (no fee), 0.05 (published sports default), 0.07 (stress), plus 5%
adverse entry-price sensitivity. These are **SCENARIOS**; per-market
actual fee metadata can differ, and the rate used at execution is
not proven from a later fee endpoint. Do not label these 'actual net'.

- [Polymarket fees](https://help.polymarket.com/en/articles/13364478-trading-fees).
- [Polymarket API fee-rate discrepancies](https://github.com/Polymarket/py-clob-client-v2/issues/107):
  do not use `/fee-rate base_fee=1000` as proof of a flat 10% fee.
- No private wallets, transaction signing, orders, Telegram messages or
  changes to the old v3.1 / v3.2 production states.

A zero-sample model returns **null** for ROI/PnL, not 0%: zero
prospective quotes is missing evidence, not even or unprofitable returns.
As sports events resolve, the existing hourly truth audit and net
paper ledger will be able to show paired sent-vs-blocked results.
`actual_net_copy_roi` remains null without the subscriber's real
order fills, exact fees and exits.

## Validation

- 97/97 isolated v3.3 bot tests passed (includes same-poll evidence
  retention regression).
- 9/9 fee-adjusted forward-paper unit tests passed.
- First read-only ledger run:
  https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39017859342
  succeeded, truthfully reporting null PnL, because the **old saved
  state already lacked those previously erased quotes**.
- v3.3 production monitor was restarted via
  https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39018175791
  to load the cleanup fix. Confirm nonzero newly persisted
  `signal_evidence`/`shadow_evidence` on an actual live checkpoint
  before claiming end-to-end reliability. A "success" unit test is not
  a filled trade or verified return.
