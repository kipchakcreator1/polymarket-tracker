# v3.3 sports-wallet expansion: capacity canary and bounded live pilot

**Date / data captured:** 2026-10-18 UTC. Read-only Polymarket v2 pages. There were no real orders and no research-worker Telegram messages.

## Safe selection inventory

Current discovery holds **12,000** wallet metadata entries. A research-only, recent-sports-BUY-first queue has **1,200** entries of which **911 were not in the previously persisted 300-wallet runtime cursor set**. The discovery data comprise sampled market-trade observations, not independent complete wallet economics, audited sports-only PnL, or proven follower ROI.

[Offline selection proof](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39116369311): using the real recent `v3.3-wallet-research` shortlist, the original 300 is a strict subset of the pilot **346 qualifying wallets**, i.e., **46 additions**, including **26 with sampled sports BUY within the prior 24 hours**. The four previously quarantined wallet addresses remain excluded.

## Public v2 pagination load with 2 workers

[Confirmed CI benchmark run](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39115955020):

- 60 distinct wallets outside the running top 300 were queried with an explicitly fixed timestamp cutoff: 48 for prior 1 hour; 12 sampled-busy wallets for prior 6 hours.
- 84 public `/v2/trades` page requests in 12.687 seconds; **zero observed API errors or 429**, two workers; median page 0.2446 seconds, p95 0.3838 seconds, max 0.6236 seconds.
- **56** wallet query windows returned a terminal `pagination.has_more=false`. Six of those were multi-page. Four windows hit the five-page ceiling, and MUST remain UNKNOWN. The 2967 BUY rows across complete windows may be nonunique across observational sampling and are not certified sports-only or economically copyable.
- A bounded follow-up on exactly those four incomplete original queries, with the same cutoff and up to 12 pages each, issued **41 more public page requests** in 5.344 seconds. **Two more** query windows were terminal; **two remained incomplete after 12 pages**. No detected rate limit. The first five pages are necessarily reread in this conservative replay. **A single completed one-hour/six-hour window is NOT complete wallet PnL or full trade history.**
- The two unresolved heavy wallets rule out any assertion of perfect coverage at five or twelve pages. P95/latency of a small two-worker run cannot establish sustained 1,000-wallet freshness, provider request budgets, risk/portfolio API latency, or late-fill behavior under continuously running production.

The audit suite passed **231/231 offline regressions** on the canary run. The 60-wallet and deeper canaries are now **opt-in** only with an explicit `[capacity-canary]` commit marker or workflow dispatch; routine audit pushes do not repeatedly run stress queries against the production-shared public API.

## Narrow live config pilot, not an unbounded 1,000-wallet upgrade

[PR #3](https://github.com/kipchakcreator1/polymarket-tracker/pull/3) merged into `main`. It changes **only two** `V33_MONITOR_MAX` occurrences from 300 to 350 (offline validation environment + live monitor environment) and a rollback note. The executable's existing ceiling is 350, shortlist remains 350 and the sample currently contains 346 risk-qualified addresses. **The risk vetoes, orderbook copy-price checks, topical sports/BTC restrictions, banned political markets, and real-order prohibition are unchanged.**

This merge configures a **bounded pilot**; it does not prove that an already-running monitoring process changed its current wallet membership. The new push-triggered 50-minute session should check out the refreshed research branch and its own last_run must show the true selected count. The next operational gate is measuring actual `last_run.watching`, `last_run.request_errors`, `last_run.poll_gaps`, session failure counts, BUY/OPEN alert unique wallets and observed latency.

**Rollback:** change both `V33_MONITOR_MAX` values to `300` in `.github/workflows/v33-telegram.yml` if polling gaps, error rate, queue delays or repeated alerts worsen. No claim of a profitable copy strategy follows from expanded observation alone. A 1,000-wallet live rollout is NOT authorized by these data.
