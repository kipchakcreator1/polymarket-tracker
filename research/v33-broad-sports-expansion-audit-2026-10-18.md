# v3.3: broader sports-wallet research snapshot and 24-wallet API canary

**Date:** 2026-10-18 UTC. Source: read-only branch `v3.3-wallet-research`; code and JSON artifacts from the isolated `audit/v33-risk-gate-20261017` workflow.

## What was actually changed

- The user's repeated OPEN notification defect was fixed in a minimal **two-file PR #2** and merged into `v3.3-wallet-research`: https://github.com/kipchakcreator1/polymarket-tracker/pull/2 . Telegram acknowledgement can occur after the initial poll timestamp; the old code prematurely erased the freshly acknowledged OPEN deduplication record. A 24h regression confirms it persists. The already-running 50-minute workflow retains its checkout until the next runner session.
- New `tools/v33_broad_sports_research.py` **read-only** research queue built from the full saved 12k wallet universe, not from the old capped 350-person closed-PnL shortlist. It never grants live approval, changes cursors, places orders, invokes Telegram, or weakens risk gates. This is a **research observation priority**, not a verified profitable wallet ranking.
- A tiny `tools/v33_broad_sports_capacity_canary.py` reads one `/v2/trades` page over the prior 60 minutes per candidate for up to **24 previously unmonitored wallets**, using two workers. It flags incomplete paginated responses rather than treating the single page as complete.

## Observed snapshot

[Completed audit CI run](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39115199586) — **217/217 offline regression tests succeeded** (108 BUY, 9 OPEN, 5 v2 API, 9 first shadow, 7 broad queue, 4 capacity canary, 37 truth, 3 shadow probe, 11 copyability, 10 archive, 14 paper cohorts). The run's non-secret public-API sample is separate from these mocked tests.

- Full public-discovery universe: **12,000 wallet metadata entries**.
- Wallets with *sampled* sports BUY activity: **687 in 24h**, **1,248 in 48h**, **1,363 in 7d** at the audit observation timestamp. This discovery sample is not a complete on-chain wallet history, independent trade count or verified sports edge.
- **1,200 wallets** selected for the research queue based on recent sampled sports activity: 687 within 24h and 513 additional within 48h. No inactive wallet is filled into the queue merely to reach 1,200.
- **911 of 1,200** were absent from the last saved 300-wallet monitor cursor snapshot; **336 of 1,200** were inside the old 350-person shortlist. Last saved runtime cursors need not equal exact live monitoring membership at every instant.
- Among all 1,363 sports-seen-in-7d wallets, old CLOSED-only scoring: 576 pass, 672 fail, 115 incomplete; those are legacy snapshot labels, not verified economic results or live approvals.
- **0 wallets auto-promoted** into Telegram live flow or cleared of risk checks.

### The 24-wallet public API probe

- 24 single-page public v2 /trades requests, 24 successful HTTP/API envelopes, **zero observed probe errors**, **20 pages had `has_more=false`** (a fully paginated queried one-hour window), **4 had `has_more=true`** and were marked incomplete.
- Observed single-request latency: median **0.15s**, maximum **0.26s** on this small set/run. These are measurements of one page only; not the full end-to-end candidate, risk/portfolio, book, and Telegram pipeline.
- 288 sampled BUY rows from the 20 fully paginated one-hour windows; these raw rows have **not** been classified as sports/BTC, event-distinct, copyable, or profitable. The four partial pages cannot be treated as zero.
- The canary cannot establish sustained Polymarket API throughput, rate limits, p95/p99, or execution feasibility for 1000 simultaneous wallets.

## Meaning for further rollout

The live v3.3 workflow has `V33_MONITOR_MAX=300`, and the current `MAX_WALLETS` Python hard-cap is 350. The universe research itself generates only **350** historical-profitability-filtered wallets, despite storing 12k. Increasing only one env var does not yield 1k sports-live wallets.

Polling 1k wallets **every full monitoring cycle** needs at least 700 additional one-page wallet requests vs the current 300, and can require multiple cursor pages. Any actual upgrade must first prove rate/latency budget with progressive 50/100-wallet canaries and complete pagination, plus deterministic per-candidate funnel and strict fail-closed risk checks. We have *not* authorized nor implemented a 1000-wallet live Telegram rollout. The main bot's watchlist is still 300; the research queue is 1200.

**Next checkpoint:** benchmark representative heavy paginated sports wallets; keep separate counts of discovered trades / confirmed sports / ask verified / historical risk eligible / independently copyable; use staged monitoring only after API budget and completeness show it will not starve the 300 existing wallets.
