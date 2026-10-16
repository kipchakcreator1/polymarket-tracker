# Full repository-wide audit (2026-10-16)

Scope: **all 5 GitHub branches**, **71 unique file paths** in the recursive trees (`main`: 24; `v3.3-wallet-research`: 46; `tracker-v3.2-testing`: 38; `kalshi-99c-paper`: 26; `kalshi-12h-research`: 36). Walked active live code and workflow paths, production/testing isolation, archived code, tests and state formats. Two oversized raw data dumps (named below) could not be fetched verbatim by the connector; their Git blob metadata, producers and downstream schemas were reviewed. This is **not** an exhaustive audit of every individual raw trade in those dumps.

## Major verified findings
1. **Wrong testing vs production minimum:** the v3.3 workflow uses `V33_MIN_TRADE_USD=35`, but Python previously defaulted to 50 in test runs. New regression tests failed and **blocked live monitoring** in run [38872090669](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38872090669). Aligned source default and live-test environment to 35, plus end-to-end tests. Offline CI [38874344965](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38874344965): **69 tests passed**; rebuilt live runner [38874344896](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38874344896) entered the monitoring step.
2. **Discarded small fills:** `deliver()` deleted under-threshold BUY pending entries after each poll. This could silently lose $20 + $22 bought shortly apart. Keep rolling 180-second accumulation, reset if older, and only send after the $35 qualified threshold. Two unit tests and a true two-pass main/checkpoint/Telegram test cover this.
3. **Telegram API status lacked receipt validation:** shared `smart_tracker_v2.send_telegram()` used only `ok`. It now also requires a numeric `message_id` and, for numeric destinations, the expected Telegram chat ID, without logging destination or token. v3.3 records the harmless server acceptance ID and type for BUY/heartbeat. **Server acceptance is not proof that the user's app displayed a push notification.**
4. **Zero alerts is often filtering, not dead polling:** 250 wallets continuously monitored; latest pre-patch state had 144 lifetime confirmed BUY sends. At the scan snapshot, 2026-10-16 California day had **two** server-recorded BUYs (00:37 and 01:19 local), while the user's phone showed zero. Investigate Telegram app/chat/mute status if missing. Accumulated rejection counts were dominated by thousands of subthreshold buys, bidirectional trades and high-price buys; counters are not independent trades, outcomes or expected returns.
5. **GitHub Actions schedule not real-time:** 5-minute cron does **not** guarantee a start every five minutes. 50-minute monitoring sessions reduce holes but queue/cancel records show frequent delayed pending runs. A hosted runner can stop. No paid VPS or premium cloud is enabled.
6. **Five-branch separation:** only `main` schedules workflows; v3.3 checks out code and writes signal state on `v3.3-wallet-research`. v3.1 and v3.2 original trackers have schedule paused and can be launched manually. Kalshi 99¢ paper workflow still has its own schedule and uses the *v3.2 Telegram token for paper-only notifications*, separately from v3.3; it is not the old v3.2 tracking schedule.
7. **No validated copy profitability:** historical `winrate` is profitable *closed positions*, not prediction accuracy or user copy PnL. The 10k-wallet universe is narrowed to 250 ranked accounts; this is a small, biased monitored subset. The 5-dollar orderbook sweep simulates liquidity and can go stale; no real fills, real orders or wallet keys.
8. **Documentation drift:** legacy `README.md` represented original v2 as always-on realtime. Updated its top-level notice to describe the actual v3.3 scheduler, research checkout branch and no-guarantee policy.

## Files inventoried — every path
| Path | Purpose / depth | Branches |
|---|---|---|
| `.env.example` | repository metadata | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `.github/workflows/digest.yml` | GitHub Actions workflow | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `.github/workflows/kalshi-12h-research.yml` | GitHub Actions workflow | `main` |
| `.github/workflows/kalshi-12h-validate.yml` | GitHub Actions workflow | `kalshi-12h-research` |
| `.github/workflows/kalshi-99c-paper.yml` | GitHub Actions workflow | `main`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `.github/workflows/tests.yml` | GitHub Actions workflow | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `.github/workflows/tracker-v32-telegram.yml` | GitHub Actions workflow | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `.github/workflows/tracker.yml` | GitHub Actions workflow | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `.github/workflows/v33-recent-api-probe.yml` | GitHub Actions workflow | `main` |
| `.github/workflows/v33-signal-tests.yml` | GitHub Actions workflow | `main` |
| `.github/workflows/v33-telegram.yml` | GitHub Actions workflow | `main` |
| `.github/workflows/v33-wallet-research.yml` | GitHub Actions workflow | `main` |
| `.github/workflows/wallet-shadow-audit-once.yml` | GitHub Actions workflow | `main` |
| `.gitignore` | repository metadata | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `CONTRIBUTING.md` | documentation | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `copy_quality_v32.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `db.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `docs/COPY_QUALITY_V32_RU.md` | documentation | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `kalshi99/diagnose.py` | Python source / dependency | `kalshi-99c-paper`, `kalshi-12h-research` |
| `kalshi99/experiment12h_telegram.py` | Python source / dependency | `kalshi-12h-research` |
| `kalshi99/experiment12h.py` | Python source / dependency | `kalshi-12h-research` |
| `kalshi99/loop12h.py` | Python source / dependency | `kalshi-12h-research` |
| `kalshi99/README.md` | documentation | `kalshi-99c-paper`, `kalshi-12h-research` |
| `kalshi99/scanner.py` | Python source / dependency | `kalshi-99c-paper`, `kalshi-12h-research` |
| `kalshi99/telegram_report.py` | Python source / dependency | `kalshi-99c-paper`, `kalshi-12h-research` |
| `kalshi99/test_experiment12h_telegram.py` | offline test | `kalshi-12h-research` |
| `kalshi99/test_experiment12h.py` | offline test | `kalshi-12h-research` |
| `kalshi99/test_loop12h.py` | offline test | `kalshi-12h-research` |
| `kalshi99/test_scanner.py` | offline test | `kalshi-99c-paper`, `kalshi-12h-research` |
| `kalshi99/test_telegram_report.py` | offline test | `kalshi-99c-paper`, `kalshi-12h-research` |
| `LICENSE` | repository metadata | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `notifier.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `paper_tracker_v31.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `polymarket_api.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `README.md` | documentation | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `research/kalshi-12h-snapshots.jsonl` | large raw snapshot: metadata/schema/producer inspected; full dump not retrieved | `kalshi-12h-research` |
| `research/kalshi-12h-state.json` | persisted state / research data | `kalshi-12h-research` |
| `research/kalshi-12h-telegram.json` | persisted state / research data | `kalshi-12h-research` |
| `research/kalshi-99c-state.json` | persisted state / research data | `kalshi-99c-paper`, `kalshi-12h-research` |
| `research/kalshi-99c-telegram-state.json` | persisted state / research data | `kalshi-99c-paper`, `kalshi-12h-research` |
| `research/shadow-15h-trigger.json` | persisted state / research data | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `research/V33_GRAND_AUDIT_2026-10-15.md` | persisted state / research data | `v3.3-wallet-research` |
| `research/v33_signal_state.json` | persisted state / research data | `v3.3-wallet-research` |
| `research/v33_wallet_shortlist.json` | persisted state / research data | `v3.3-wallet-research` |
| `research/v33_wallet_universe.json` | large raw snapshot: metadata/schema/producer inspected; full dump not retrieved | `v3.3-wallet-research` |
| `research/wallet-copy-audit-2026-10-14.md` | persisted state / research data | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `research/wallet-shadow-v32-2026-10-14.json` | persisted state / research data | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `smart_tracker_v2.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `smart_tracker_v3.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `smartmoney.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `state/smart_tracker_v3.json` | persisted state / research data | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |
| `tests/test_copy_quality_v32.py` | offline test | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tests/test_shadow_qualified_v32.py` | offline test | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tests/test_sweep_market_wallets_v32.py` | offline test | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tests/test_tracker_v32.py` | offline test | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tests/test_v32_score_refresh.py` | offline test | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tests/test_v32_telegram_route.py` | offline test | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tests/test_v33_signal_bot.py` | offline test | `v3.3-wallet-research` |
| `tests/test_v33_wallet_universe.py` | offline test | `v3.3-wallet-research` |
| `tests/test_wallet_forensics_v32.py` | offline test | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tools/check_market_discovery_api.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tools/check_wallet_forensics_api.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tools/dry_run_v32.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tools/shadow_qualified_v32.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tools/sweep_market_wallets_v32.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tools/v32_telegram_entry.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `tools/v33_signal_bot.py` | Python source / dependency | `v3.3-wallet-research` |
| `tools/v33_wallet_universe.py` | Python source / dependency | `v3.3-wallet-research` |
| `wallet_discovery_v32.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `wallet_forensics_v32.py` | Python source / dependency | `v3.3-wallet-research`, `tracker-v3.2-testing` |
| `whale_tracker.py` | Python source / dependency | `main`, `v3.3-wallet-research`, `tracker-v3.2-testing`, `kalshi-99c-paper`, `kalshi-12h-research` |

## Active v3.3 critical path
`main:.github/workflows/v33-telegram.yml` → checkout `v3.3-wallet-research` → `tests/test_v33_signal_bot.py` → `tools/v33_signal_bot.py` → `smart_tracker_v3.py` (v2 trades + position checks), `smart_tracker_v2.py` (HTTP + Telegram), `paper_tracker_v31.py` (read-only ask/bid simulation) → `research/v33_signal_state.json`. Separate hourly discovery: `main:.github/workflows/v33-wallet-research.yml` → `tools/v33_wallet_universe.py` → `research/v33_wallet_universe.json` + `research/v33_wallet_shortlist.json`. A yellow/green test does not itself prove market quality or a phone's push delivery.

## Post-change acceptance checklist
- The **live v3.3** job's offline test stage passes, then **Monitor shortlisted wallets** executes.
- Check `last_run.new_fills`, `last_run.alerts`, `last_run.this_poll` and `counters.alerts_sent` after live calls; confirm a nonzero real Telegram receipt from the next genuine BUY or hourly status.
- Check that partial $20+$22 within 3 minutes is aggregated; >3-minute partials are not combined. No duplicate source trade IDs.
- Do not claim profit, push delivery or uninterrupted running based on a successful CI test alone.
- Cost remains **$0**: public APIs, public GitHub repository standard hosted Actions, Telegram. No paid hosts/subscriptions or trades.

Created as a static forensic inventory; no external trading action or secrets involved.
