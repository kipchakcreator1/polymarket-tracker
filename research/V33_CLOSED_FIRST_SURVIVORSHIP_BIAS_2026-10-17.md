# v3.3 Critical sports winrate research correction (2026-10-17)

## Finding: closed-status survivor bias caused false-perfect historical samples

Discovered **after** the first 12-wallet lagged-price research trial.

The old `tools/v33_result_audit.py::fetch_position_history` loads `CLOSED`
before `REDEEMABLE`. Then `position_truth_sample` grouped records into a
dictionary in that order and checked only `list(markets.items())[:220]`.
For wallets with more than 220 CLOSED positions, resolved but unredeemed
losses were frequently **never looked up**, because the 220-slot budget
was consumed by CLOSED positions first.

This is a selection flaw: even if all *observed* 30 resolved outcomes have
correct V2 payout verification, a 30/30 success fraction is meaningless
when unredeemed losing positions were excluded by API response status.

Examples from saved research report (before fix):

| Wallet | CLOSED positions | REDEEMABLE losing positions | Observed win/loss of 30 | Historical scan complete? |
| --- | ---: | ---: | --- | --- |
| `0x300317934bfe0cc8740affc7425b0cd3772530f5` | 523 (511 profitable) | 533, observed -$18,921.99 | 30 / 0 | NO (1,047 conditions, first 220 used) |
| `0x6b1dbe91847aa95386fc63669ff910e68469a01a` | 389 | 288, observed -$26,450.07 | 29 / 1 | NO (677 conditions, first 220 used) |
| `0xa772e2ca9f119162d03330f3e66c0556a2068686` | 404 | 297, observed -$20,972.82 | 30 / 0 | NO (701 conditions, first 220 used) |
| `0x31550df5402226b825f900a31659a05d809d268e` | 162 | 18, observed -$10,098.55 | 21 / 9 | YES for position market enumeration (157 conditions), but still 31 unresolved/ambiguous final records |

**Important:** `REDEEMABLE losing positions` is raw mixed-category position PnL
and **not** necessarily the count of incorrectly predicted sports outcomes.
Likewise, 511 profitable closures are not 511 correct sports predictions.

## Fix

1. Before the bounded 220-condition market scan, **merge both statuses**
   into a single condition inventory.
2. Sort by the **timestamp of the first actual BUY** (maker+taker trade
   history), descending. Only use position activity timestamps as an
   exploratory fallback when the original BUY is unavailable.
3. Report the number of scanned conditions of each status and mark
   truncated inventories incomplete. Classification still verifies the
   exact token's finalized payout using Polymarket Data API v2.
4. Added a targeted regression test: 221 CLOSED profitable positions +
   a more recent REDEEMABLE $600 sports loss. The loss **must** enter the
   scan and the source must **not** be certified as complete.
5. Added mandatory `position_scan_selection` provenance to the new
   multiwallet lag study. Previous observational positive labels are
   **retracted**: `research/v33_wallet_copy_cohort.json` is explicitly
   marked `invalidated_due_to_source_bias`. Only data from reaudited
   wallets with the new timestamp-mixed scan is allowed in future
   copy-price research.

Validation: live GitHub Actions CI run
https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39015906212
passed the revised offline truth tests and the independent archived UFC
settlement spotcheck. This proves the regression is tested, **NOT** that
the live sportsbook signals or all wallet rankings are now profitable.

## What to trust

- The raw presence of substantial settled unredeemed losing positions
  and the need to account for them.
- Exact official v2 outcome-token settlement for independently matched
  positions, including old UFC loser verification.
- The historical CLOB sampled-price evidence **ONLY AS AN INVALIDATED,
  selected-source experiment** until the reaudited wallet cohort exists.
- Neither an available sampled market price nor a price inflated by 5%
  establishes the ASK, depth, execution, actual fills, fees or profitability
  of copying.

A fresh complete research audit for the affected wallets is launched from
the main-branch `v33-sports-truth-research.yml` workflow. New results must
be compared to this prior biased baseline **before** any wallet promotion.
No automatic Telegram/ranking change is authorized by this experiment.

## Independent newest-first-BUY outcome validation on LIVE data

[GitHub Actions independent 3-wallet recent BUY audit](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39016415904)
ran against current public Data API v2. Unlike CLOSED-first history, it
**chooses the newest 50 original BUY conditions first** based on trade
timestamps, irrespective of PnL or position status, then attempts to
independently verify token settlements. Zero trade execution.

| Wallet | Verified WIN | Verified LOSS | Other BUYs in selected 50 | Unredeemed negative positions among those BUYs |
| --- | ---: | ---: | ---: | ---: |
| `0x300317934bfe0cc8740affc7425b0cd3772530f5` | 13 | 11 | 20 missing position; 6 not verified sports | 15 |
| `0x6b1dbe91847aa95386fc63669ff910e68469a01a` | 15 | 20 | 11 not verified sports, 2 missing, 2 incomplete settlement | 26 |
| `0x31550df5402226b825f900a31659a05d809d268e` | 30 | 16 | 3 missing position, 1 push/partial | 16 |

These **ARE NOT** full sports winrates, or the last 50 *completed* sports
events. Denominators for verified directional win fractions are 24, 35 and
46 respectively. Unknowns are not wins and cannot be silently omitted when
claiming fully verified signal performance. Correctness says nothing about
copy profitability without the actual attainable historical ASK/depth.

The previous older samples for two of these wallets were 30/30 and 29/30
from a biased truncated status-first inventory. **No direct percentage
comparison** is valid because the cohorts and selection windows differ.
The new results demonstrate why the old confidence was spurious.

Four risk-heavy wallets were set as temporary quarantine environment
variables in the live `.github/workflows/v33-telegram.yml` on top
of its existing general loss filters: the UFC case wallet above plus
`0x3003...`, `0x6b1d...`, and `0xa772...`. This is a
provisional risk block, not a finding that those wallets can never be
profitable. Tests and reauditing are independently tracked in Actions.

## Four additional shortlisted wallets

The same recent-first-BUY test was run on four other shortlisted wallets:
[GitHub Actions run 39016604038](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39016604038).
Each 180-day maker-inclusive trade feed and both positions status feeds were
reported complete.

| Wallet | Recent original BUYs inspected | V2 verified sports WIN | V2 verified sports LOSS | Other 50 BUYs | Held negative positions within inspected buys |
| --- | ---: | ---: | ---: | --- | ---: |
| `0xee8a3de6ca0080af2abc5c9acbfc4dddadb269ac` | 50 | 4 | 2 | 36 not verified sports, 8 position absent | 10 |
| `0x69f033ce27733d8717d7d168a3eb8a91610a1121` | 50 | 11 | 4 | 33 not verified sports, 1 position absent, 1 incomplete resolution | 8 |
| `0x993700f76fb8e1fd8e0846afe3850d31f7cc4e38` | 50 | 23 | 12 | 4 position absent, 11 incomplete resolution | 21 |
| `0x906aff5eb0c700a1fa2dee7c1b766c3384a4714e` | 50 | 4 | 5 | 21 not verified sports, 20 position absent | 13 |

These are **not** certifiable all-time/last-50 sports winrates or proof of
profitable copying. Sample is latest 50 original BUYs per wallet; some events
may not have resolved or may lack a matching *held* position.
The win/loss denominator varies by wallet. Any score based on only positive
closed positions is misleading. This study doesn't know what a subscriber
could pay for market-order fills.

Even among wallets with apparently high recent sports win proportions,
verified sample sizes (4/6 and 11/15) are too small and position loss
inventory is nontrivial. No automatic live BUY promotion was made.
