# Kalshi 98-99¢ Scanner — $4 PAPER-ONLY study

Separate prototype: no Polymarket dependencies, no trading/account API keys,
no deposits, no order placement, no real-money betting, no copied wallets.
Production Tracker 3.1 and experimental 3.2 are unchanged.

## What it does

- Reads up to 12 pages × 500 Kalshi markets **closing in the next 7 days** and requiring an `active` market status (not the entire platform). Excludes combo markets.
- Shortlists YES/NO with indicative **asking prices** of $0.98–$0.99 and close times in 10 minutes–72 hours.
- Fetches each shortlisted market's **public orderbook** (max 25 per run).
  Kalshi exposes bids, not asks. An actual YES buy ask is `$1 - highest NO bid`,
  and a NO buy ask is `$1 - highest YES bid`; depth is checked at that exact price.
- Estimates **standard taker commission**, conservatively rounded up to a whole
  cent for every hypothetical order. Market-specific fee schedules and other
  charges can differ. All P&L values are estimates.
- Reports price, top-level available quantity, fee estimate, hypothetical net
  gain on a win, loss on a miss, and the **minimum actual success probability
  required just to break even**.
- If run with `--paper-trade`, reserves no more than the virtual **$4**,
  keeping at most one position open, holding to official settlement. No
  simulated early exits, no unearned settlement credits, no replay fills.
- Only settles on an API response with `status=finalized` and
  `result=yes/no`. Other results are kept pending for manual examination.
- Remembers previously tested markets to avoid double-counting one market side.

## WARNING: NOT AN EDGE MODEL

**No independent probability model is implemented.** A 99¢ ask does *not*
demonstrate a 99.9% probability; 98–99¢ is only a price-based *baseline*.
This scanner cannot decide which 99¢ bets are "safe." Winners can still
cause misleading conclusions because rare losses require large samples.
Observed fills are hypothetical: prices can disappear before an order
executes, and there is no proof these contracts would have filled live.

The only mathematically defensible profitability test is a large set of
fully settled positions with accounting for execution, fees, and selection
bias, ideally evaluated against independent forecasts made *before* outcomes.

## Run locally

Python 3.11+, no pip dependencies or login.

```bash
python -m unittest kalshi99.test_scanner -v
python -m kalshi99.scanner --pages 6 --page-size 500 --books 25
# explicitly opt in to VIRTUAL buying; this never sends any real order:
python -m kalshi99.scanner --paper-trade
```

- Paper state: `research/kalshi-99c-state.json` (first run starts at $4.00)
- JSON snapshot: `artifacts/kalshi-99c-latest.json`
- Run without `--paper-trade` to inspect opportunities without entering.

## GitHub Actions

`.github/workflows/kalshi-99c-paper.yml` runs tests + read-only public API
study on code pushes to `kalshi-99c-paper`, uploads the report as an Actions
artifact, then commits **paper state to the research branch only**. An isolated main-branch workflow schedules the public-data paper scanner
approximately every 5 minutes and checks out this research branch. Cron runs
may be delayed. All state writes go to the research branch; other bots and their
tokens are not used.

## Source API and fees

- Public markets: https://docs.kalshi.com/api-reference/market/get-markets
- Public orderbook: https://docs.kalshi.com/api-reference/market/get-market-orderbook
- Public settlement: https://docs.kalshi.com/api-reference/market/get-market
- Actual fees: https://kalshi.com/fee-schedule

Paper fee formula uses conservative `ceil_to_cent(0.07 × contracts × price × (1-price))`,
not a guaranteed exact commission. The production trading API is neither
imported nor called.
