# v3.3 — Verified sports direction and hypothetical copied-entry PnL

**Purpose:** Retire the false interpretation of "profitable closed positions" as "sports predictions that won." This document describes deployed **research** and forward paper evidence, not a proven strategy.

## Observed problem in original wallet ranking

- \`smart_tracker_v2.closed_stats\` uses \`/closed-positions\` and counts \`realizedPnl > 0\`. This is **profitable position exit rate**, not game prediction accuracy.
- A user who buys Lakers at 0.40, sells at 0.65, and whose Lakers eventually lose has **positive realized PnL** but **incorrect final direction**.
- \`tools/v33_signal_bot.py:recent_form\` uses v2 \`last_event_at\` (last position activity), **not** match settlement time. \`last5\` is not necessarily the last five completed games.
- \`REDEEMABLE\` is not a synonym for winner; settled/unredeemed losing holdings must be retained.
- A 90% closed-position rate pooled across politics, crypto and sports cannot justify 90% sports outcome accuracy.
- The performance of a $5 copying strategy is **not** the underlying wallet's realized PnL; it depends on OUR ask and exit.

## Independent read-only pipeline

Scheduled GitHub workflow: \`.github/workflows/v33-sports-truth-research.yml\`
(defined in **main**, checking out the separate \`v3.3-wallet-research\` branch).

Code: \`tools/v33_result_audit.py\`. Tests: \`tests/test_v33_result_audit.py\`.
Output: \`research/v33_truth_report.json\` on research branch.

Sources:
1. Public \`/v2/trades?user=\` with cursor and a bounded completeness indicator.
2. \`/v2/positions?user=&status=CLOSED\` and \`status=REDEEMABLE\` with complete-vs-partial pagination explicit.
3. Gamma \`/markets?condition_ids=\` to verify official sports category and **outcome-to-token index**, accepting JSON-encoded token arrays.
4. Official \`/v2/resolutions?condition=\` only when \`status=resolved\` and a final payout vector with resolved timestamp is supplied.

Hard rules:
- Never use Gamma's current/stale \`outcomePrices\` as final results.
- Count verified **market/outcome directions**, one per condition. Opposite buys in one condition are ambiguous; exclude. One market is not necessarily one distinct game.
- Use \`resolved_at\` for settlement ordering, not \`last_event_at\`.
- Partial/50:50/invalid payout, disputed markets, missing official sports category/token mapping and API errors are **not wins or losses**.
- Missing, truncated histories must never be silently converted into a winrate.
- Keep "profitable CLOSED + direction LOSS" as a documented mismatch, not a fabricated win.
- Report observed fraction separately from "certified_sample". \`certified_sample\` needs >=20 decisive observations AND a complete corresponding history; still only descriptive, not a causal/forward profitability guarantee.
- Future copy-paper evidence captures simulated ~$5 ask at Telegram alert acceptance; no real order is placed. When that precise entry exists, hypothetical hold-to-settlement PnL is estimated **before fees**, assuming the $5 purchase happened at the quote and was held through resolution. This is neither a historical fill nor realized user profit.
- The old alerts never recorded their executable entry asks: historical copy ROI is irrecoverable; do not backfill from post-event prices.

## Integration policy

**Do NOT switch the live wallet ranking or loosen existing risk gates based on only a handful of newly observed resolutions.** Current historical closed-position filters continue only as weak preselection and risk proxies; they must not be advertised as a confirmed sports prediction winrate.

A future production ranking change requires: enough verified sport-specific history by wallet and league, calibration against the quoted implied probabilities and fees, an out-of-sample forward paper study of actual alert asks, adequate sample size and maximum drawdown review. 20-30 observations are a starting diagnostic, not a statistical proof or a guarantee of perfect picks.

Old v3.1/v3.2 code and Telegram routes remain untouched. The research workflow does not send Telegram and does not place any order.

## October 17: independent UFC case-study / root cause

Focused wallet: `0x31550df5402226b825f900a31659a05d809d268e`.
A complete 180-day maker-inclusive `/v2/trades?user=&taker_only=false&start=&end=`
walk returned 345 fills over 91 markets. The previous study marked all 91 as
unknown, despite visible resolved losing UFC holdings.

Root cause: archived markets sometimes no longer appear in Gamma
`/markets?condition_ids=...`. A **real independent live sample** (GitHub
Actions run 39002719756, attempt 2) found:
- UFC Alvin Hines: v2 `REDEEMABLE_LOST` unrealized PnL -$601.7637,
  condition `0x3f3edc42fd8943...`, and `/v2/resolutions` reported
  `status=resolved`, `payouts=[0,1000000]`,
  `resolved_at=2026-07-18T22:00:28Z`.
- UFC Gauge Young: v2 unrealized PnL -$600.9928,
  condition `0x3cdba77ef9049d...`, final `[0,1000000]`,
  `resolved_at=2026-08-23T01:15:48Z`.
- Gamma lookup produced no matching market for either case.

Repair: join `/v2/tokens?token_id=` to the exact `condition_id` and
`token_id`, require official event-sports slug, `resolved=true`,
`closed=true`, a valid outcome index (never sentinel 999), and consistency
with final `/v2/resolutions` payouts and any `final_price`.
Fail closed on any missing/mismatched evidence, use Gamma only when it returns
a matching live/historical market. Do **not** infer a lost/winning pick solely
from mark-to-market PnL or stale prices.

Other repairs: 180d time-window complete cursor walk including maker and taker,
refuse out-of-window response, count first BUY timestamp (not first SELL),
mark cutoff at 140 researched conditions, audit archived REDEEMABLE/CLOSED
inventory and count large unredeemed losing holdings separately from profitable
CLOSED exits. See `tools/v33_result_audit.py`.

**Deployment boundary:** This is READ-ONLY research. It neither sends Telegram
signals nor certifies existing 71–90% general closed-position profitability as
sports prediction accuracy. Live v3.3 remains on its existing provisional
wallet ranking until sufficient verified per-sport outcomes and forward
hypothetical copied-entry ROI support a controlled replacement.
