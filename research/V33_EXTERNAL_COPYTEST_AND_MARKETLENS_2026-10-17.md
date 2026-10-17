# v3.3 external copy-trading engines: independent reproducible comparison (2026-10-17)

**READ-ONLY research. No wallet private keys, actual fills, orders, or Telegram sends.**

## Existing open-source components checked

- [howwohmm/polymarket-paper](https://github.com/howwohmm/polymarket-paper):
  implemented 0, 1, 5 and 10 minute lag sensitivity inspired by its
  `copytrade.py` approach. Its original `copytest` infers winner from a
  near-0/near-1 terminal historical price, which is insufficient for archived
  or disputed sports outcomes. Our replacement uses outcome direction from
  prior official Polymarket V2 token + resolution audit.
- [Marketlens Python SDK](https://github.com/marketlenstrade/marketlens-python):
  full tick-level historical order books, market depth and execution replay.
  Our no-key independent control directly parses its freely available UFC330
  `markets.csv` and `snapshots.csv` instead of assuming execution. The full
  SDK's relevant features require historical coverage of *the exact wallet
  condition at the exact time*; full older archives may require an API key/
  subscription.
- [Marketlens free UFC330 history](https://github.com/marketlenstrade/polymarket-historical-data/tree/main/ufc-330-2026-08-16):
  69 archived conditions, 69 L2 books available. Attribution: **Marketlens,
  https://marketlens.trade; data licensed CC BY 4.0**.

## Live independent runs (GitHub Actions)

- [CLOB sampled-price lag copytest](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39010591738):
  7/7 unit tests passed, 30/30 observed verified UFC positions covered by
  sampled historical prices for all four delays. Same 30-outcome cohort
  before fees:
  - 0s delay: historical **price-proxy** hold ROI +52.12%
  - 60s: +52.40%
  - 300s: +53.87%
  - 600s: +51.81%
  - 600s with 5% higher entry price: +44.72%.
  THESE ARE NOT FOLLOWER FILLS, NOT EXECUTABLE BID/ASK, NOT NET PROFIT.
  31 other markets lack complete official resolution evidence; 30 verified
  observations have selection/survivorship risk. No possible conclusion of
  wallet copy edge from those results alone.
- [Marketlens free historical real-wallet join](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/39010845054):
  5/5 unit tests passed. Wallet `0x31550df5402226b825f900a31659a05d809d268e`
  had 345 maker-inclusive fills in 180d across 91 markets. The archived
  69-condition UFC330 set overlapped **three exact conditions**: one
  officially verified win and two officially verified losses.
  **0/3** wallet entries had historical L2 available at that entry time:
  all three BUYs predated the free archive capture window. All 12
  (3 positions x 4 lags) book quote requests were correctly rejected
  as `copy_time_before_free_book_archive`. No fake book price from days
  later was substituted; execution ROI = UNKNOWN.

## Code and rerun

- `tools/v33_external_copytest.py` and
  `tests/test_v33_external_copytest.py`, workflow
  `.github/workflows/v33-external-copytest.yml`: CLOB public
  `/prices-history` explicit minute window, max 180-second observation
  staleness, latest point <= copy timestamp, official V2 payouts from
  `research/v33_truth_report.json`, no-key sampled-price sensitivity.
  Raw output available as a GitHub Actions artifact.
- `tools/v33_marketlens_archive.py` and
  `tests/test_v33_marketlens_archive.py`, workflow
  `.github/workflows/v33-marketlens-ufc330-backtest.yml`: no-key download
  of Marketlens free UFC330 CSVs; strict condition join against all historical
  candidate BUYs; as-of historical YES/NO best quote conversion;
  reject stale and out-of-coverage order books. The snapshot's top ask
  **does not prove sufficient shares to fill $5**. Raw output as an Actions
  artifact.

**Operational boundary:** neither backtest is allowed to promote wallets
or change v3.3 live BUY filtering. More historical L2 for the same 30
conditions and exact entry times is necessary to estimate realistic copy
execution. We can use Marketlens's full archive SDK when authenticated
historical access is available. Until then, our new forward book quote
journal provides actual alert-time prospective price/size conditions, and
the script presents price-only historical proxies explicitly as such.
