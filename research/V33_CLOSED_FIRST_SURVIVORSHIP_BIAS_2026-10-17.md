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
