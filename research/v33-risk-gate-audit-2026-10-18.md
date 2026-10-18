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

## New research-only shadow top-300 validation queue

A **separate** program `tools/v33_research_shadow_queue.py` joins only the following read-only inputs:

- `v33_wallet_shortlist.json` — legacy ranking of profitable CLOSED sample (not predictive, not lifetime economics).
- `v33_evidence_rank.json` — distinct verified sports event observations, certified-history flags, ambiguous outcomes, severe unredeemed loss inventory.
- `v33_wallet_copy_cohort.json` — selected historical price-proxy lag tests, *not* executable prices or copy PnL.
- `v33_forward_net_paper.json` — strictly prospective $5 book-quoted paper holds, fees modeled, not filled; duplicated condition observations ignored.
- `artifacts/v33-risk-api-audit.json` — bounded public v2 risk pages, with pagination/zero-timestamp uncertainty explicitly retained.
- `v33_signal_state.json` — read-only last saved 300 wallet cursor addresses for a historical overlap comparison, **not a real-time exact inventory**.

The GitHub Action checks out the freshest available `v3.3-wallet-research` branch into `v33-snapshot` read-only, without replacing any production executable or modifying the live monitoring state. It writes the research JSON **only to a downloadable CI artifact**, not back to the production shortlist. It remains an **audit priority queue, not a new top-rated wallet list or alert allowlist**. Severe loss evidence raises audit priority; win rate or historic PnL do not buy a higher quality status.

[Successful fresh-snapshot audit CI run](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39104692004): 108 BUY/risk, 8 OPEN, 5 API-diagnostic, 9 shadow-queue offline tests passed (**130/130**). The sampled research snapshot was commit `6a2055aa79ed3197a2121d06272d8efb83d08e3d`. Data collection timestamps span about **75,567 seconds (~21 hours)**, so this is *not* a single cutoff-time backtest.

**Observed output:** 359 unique pool wallets; 300 research queue entries; 190 `HISTORY_REVIEW`; 110 `RISK_REVIEW`; 291/300 overlapping the independently reconstructed old shortlist; 287/300 overlapping last saved runtime wallet cursors; 160 old shortlist entries had a sports audit; **0 complete historical sports audits** and **0 wallet datasets with at least 30 distinct settled book-quoted conditions in this queue**; **0 live approved**. Not enough outcome and execution evidence to choose a net-profitable copy-trader.

To reproduce locally or in CI, run:

```bash
PYTHONPATH=. python -m unittest discover -s tests -p 'test_v33_research_shadow_queue.py' -v
PYTHONPATH=. V33_SHADOW_INPUT_DIR=v33-snapshot V33_SHADOW_INPUT_REF=v3.3-wallet-research python tools/v33_research_shadow_queue.py
```

The GitHub Actions run stores `artifacts/v33-shadow-wallet-queue.json` and `artifacts/v33-risk-api-audit.json` in artifact `v33-audit-risk-diagnostics` with seven-day retention. No trade orders or Telegram deliveries have occurred.

## Forward copyability and previously dropped shadow outcomes

**Scope:** The additional copyability module and archived-shadow repair are **audit-branch only**. They are not collecting new live traffic on their own, and nothing has been merged into the running `v3.3-wallet-research` monitor.

A new independent validator `tools/v33_copyability_validation.py` reads the fresh production-research snapshots `research/v33_forward_net_paper.json` and `research/v33_signal_state.json` without contacting Telegram or placing orders. It checks the original quote, depth/stake consistency, independent outcome status inherited from the official v2 audit, each paper-PnL scenario against the price formula, duplicates by market condition, and source-to-quote times only when original metadata exists. It explicitly refuses to impute missing outcomes or infer fee receipts/fills.

[Successful CI run with 178 tests](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39106429499): **108 BUY/risk, 8 OPEN, 5 timestamp/loss API, 9 shadow-queue, 37 sports truth, 11 copyability = 178/178 pass**. The tests are offline; the audit workflow additionally fetches a small public v2 sample and reports a read-only current research snapshot.

In the **snapshotted** forward paper ledger: **905 quote rows**, comprising **8 Telegram-sent** and **897 shadow-only**; **3 independently classified settled observations**, all in sent stream, outcomes **1 win, 2 losses**; **0 settled rejected observations**, **900 awaiting settlement classification**, **2 not verified/nonstandard sport**. Recalculated model HOLD on the $15 three-case sample yields **−$6.41667 (−42.7778%)** under the hypothetical 5% taker-fee scenario, and **−$6.81349 (−45.4233%)** when modeling both 5% fee and 5% worse entry. These are *not* actual fees/returns, and 3 observations are far below the 30-distinct-market descriptive threshold. For the 3 matched quote/source-trade times, observed source-trade→book-quote median was **35 seconds** (not Telegram/client receive latency). The rejected side has **no estimable ROI**, so a false-negative rate or causal filter benefit is **NULL**, not 0. Even 30 rows per stream would not solve self-selection bias or certify independent sporting events.

### Important shadow truth collector defect and audit-only repair

The existing `tools/v33_result_audit.py` code checked only **25 rejected-shadow market results each hourly run, newest first**, while the monitoring state capped `shadow_evidence` at 900 and prior shadow rows absent from the latest ring were dropped from the next truth report. Thus settled outcomes and original executable-looking quotes could be **censored before classification**.

The audit-branch collector now:

- Keeps previously seen rejected picks (up to **45 days**, subject to a **12,000-row storage cap**) with their original quote metadata, once authenticated from contemporaneous state. No later price is allowed to replace the first recorded quote.
- Checks old `not_checked` and overdue unresolved market results before recently arrived signals, with an explicit 25-per-run API budget; reports archive size, number never checked, quote capture count and cap/eviction warnings.
- Carries the original archived quote across resolution-status rechecks, and **recalculates paper PnL only from that quote**. If no quote was preserved, old PnL fields are removed rather than trusted or retrospectively imputed.
- Maintains the decision tag (blocked vs sent) across a quote's removal from the rolling state, and does not label shadows as Telegram signals or placed orders.

This upgrade is covered by five additional regression cases, including starvation, cache eviction, impossible retroactive improvement of quote price, missing original proof and time-window expiry. Passing these tests does **not** mean the running bot now archives more outcomes. Deployment and actual forward observation are still required to determine whether the backlog can drain at 25 checks/hour. This is a capacity constraint, not a claim of complete 45-day coverage. A cap-triggered eviction warning will mean outcomes remain censored.

**Next validation gate:** independently instrument or safely canary the shadow outcome collector over multiple completed sports events and show a resolved sample for both rejected and sent groups, quote-age distribution, unresolvedness/retention gaps, and paper outcomes with explicit fee assumptions. Only then compare filters; any results remain observational and not actual net copy-trading performance.

## One-time public v2 shadow outcome canary: 2 blocked wins without original prices

The [successful audit-branch probe run](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39106695551) executed `tools/v33_shadow_resolution_probe.py` using the most recent read-only `v3.3-wallet-research` snapshots and at most **10 new public settlement classifications**, without Telegram or orders. The additional **3 offline probe tests** also passed, making the run's totals **181/181 tests passed** (108 + 8 + 5 + 9 + 37 + 3 + 11).

Observed input: `shadow_evidence` current ring has **900 keys** and previously saved `shadows.rows` has **900 rows**. The audit-branch archival/recheck function retained **984 keys**, demonstrating that the old behavior silently lost references outside the current 900-entry ring. The 984 outcomes included **773 not checked, 202 unresolved/pending, 7 not verified sport, 2 verified wins**. The **two independently classified wins** belonged to `blocked_recent` shadow decisions according to the original decision snapshots (confirmed by the follow-up probe). **Neither of those two resolved wins had its contemporaneous original quote available**, therefore **NO model copy entry return or missed profit can be calculated for them**. Of the 984 retained picks, 900 had contemporaneous quote fields available and 84 did not; this is a bounded read-only trial, **not** production archival after deployment.

**Interpretation:** Some verified winning market directions were blocked by the recent-form gate; this is *not evidence that the gate has negative expected value*, because losers and unresolved outcomes remain censored, no independent event-level denominator is proven, and the two wins lack their original executable quote. The re-query is ephemeral: because this probe does not write the source truth report back to production, repeated runs may rediscover the same two wins and must **not** be counted as new wins each time.

**Remaining rollout prerequisites:** a durable on-branch research-only quote/outcome checkpoint across runs; retention/eviction alerting if >12k entries or API budget can't drain; a balanced, source-time-matched prospective cohort after sufficient settlements. Do not alter risk thresholds or announce false-negative ROI from these two cases.

## Still unresolved / next gate

- The old universe shortlist still uses up to 100 **legacy closed positions** to rank and admit 300 wallets. It is not a complete economic or predictive ranking. Keep this behavior unchanged in production pending a real, event-level and cash-flow-reconciled replacement.
- v2 REDEEMABLE_LOST alone does not substitute for wallet trade/activity ledger, ownership reconciliation, independent event outcomes, and follower execution replay.
- Validate timestamp-zero cases against official resolution history before assigning them to any time-window (24h or 90d).
- Build the next research-only **shadow shortlist** with explicit COMPLETE/UNKNOWN state, no promotion on incomplete history, and compare same-cohort forward paper results against the active bot. Existing audit changes must not be merged into live until monitoring, retry overhead, and Telegram ack behavior are verified with canary tests.
