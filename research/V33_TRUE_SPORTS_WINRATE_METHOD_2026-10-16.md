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
