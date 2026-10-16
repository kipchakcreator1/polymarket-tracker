# v3.3 sports-first + BTC-only audit, Oct 16 2026

## User requirements
- **Sports first**: prioritize real matches and sports bettors, not movies, celebrity, politics or year-out futures.
- **Only Bitcoin** in the cryptocurrency category (no ETH/SOL/XRP).
- Reject high historical scores if *observed recent position outcomes* have gone bad.
- Cost **$0**: GitHub public hosted runner, public Polymarket API, Telegram; no trades.

## Original defects
- Research sampled up to 240 generic high-volume markets rather than identifying sports specialists.
- `/closed-positions` general profitable-position rate (70%+), profit factor and $100 PnL are **not sport-specific forecasting accuracy**. A wallet can be historically positive overall and poor in its last several positions. Last activity timestamps are not necessarily settlement chronology.
- Recent live gate allowed ~55% of last 20 positive, 3 latest negative in a row and no last-five profitability check.
- Live alert topic filter was **crypto_short only**, violating the user's actual sports-first preference.
- Only 250 wallets were watched from ~12,000 discovered; ramping up API blindly risks HTTP 429. Research saving also failed once due to a concurrent Git ref push, not market quality.

## Changes
1. `tools/v33_wallet_universe.py`: query Gamma's official `/sports` list of league tag IDs, then active events per sport tag. Accept real game markets with a verified event/start/end timestamp close to now, prefer up to 120; retain 120 generic rotating markets to maintain diversity and only 6 recent BTC windows. Do not infer sports classification from words in a person's name.
2. Track `sports_last_buy` as a real trade timestamp (BUY $10+ at prices 15–85c, recently observed). Rank **qualified sports-active** traders ahead of BTC-active, then general. Protect them from future full-universe eviction. Existing 30+ closed, >=70% historical profitable positions, >=$100 closed PnL, profit factor >=1.15 remain preselection guards.
3. Expand research shortlist to **350**, live monitoring config to **300** with conservative concurrency. 12,000-wallet universe. Never assert that all 12,000 are monitored.
4. `tools/v33_signal_bot.py` sports_btc mode checks official Gamma `sportsMarketType`, `gameId` or sports category/tags before sending; rejects other categories and all non-BTC crypto. Uses game start time for sports, avoids treating sports `endDate` as the actual resolution deadline, allows in-play within a conservative 8h and games up to 7 days ahead. Official market active/order acceptance + executable book checks remain mandatory.
5. At alert time require 20 observed recent PnL entries, at least 60% positive, no 2 most-recent observed losses in a row, last 5 must have >=3 positive and net positive PnL, PF>=1.25, no excessive open unrealized drawdown/settled-but-unredeemed losses. These are *activity-ordered risk proxies*, NOT independently confirmed recent betting results.
6. Telegram message includes last-five observed positive count and PnL. Each live monitoring summary records actual sports-active and BTC-active wallets. Concurrent GitHub pushes now retry on stale ref rejection.

## Actual live-data evidence
- Research run [38888201794](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38888201794) sampled 120 real sports markets, found 220 recently active sports wallets and 44 historically qualified in a 250-person shortlist. The branch-state write failed due to GitHub push race (log explicitly says `cannot lock ref`).
- Retry-enabled research run [38888356910](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38888356910) succeeded and persisted the 44-sports subset.
- Research run [38888578000](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38888578000) succeeded, published **350** shortlist entries, with **79** recently sports-active, **19** BTC-active, 0 trade failures, and **16/16** research tests passing. Of those 79 sport-active accounts, **51** had a supported mid-price trading profile; **28** were price-profile unverified, and must still pass live book verification.
- Offline signal suite **82/82** passed [38888707085](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38888707085). Production launch [38888707033](https://github.com/kipchakcreator1/polymarket-tracker/actions/runs/38888707033) passed in-job tests and entered the monitor step, but at document time its latest persisted checkpoint still reflected the **older 250-wallet crypto** job. Treat the 300-person live-state count and new actual sports BUY messages as **awaiting confirmation**.
- The historical 147 Telegram BUY accepts were from prior configurations and NOT proof the new sports strategy works or user has seen a push notification.

## Remaining necessary proof
- Live state must show `watching=300` and `sports_recent_watching` (typically near 79, depending on the shortlist refresh).
- Measure one complete operating period: accepted **sports** and **BTC** BUYs, response delays, per-filter suppression reasons, API request failures and false-topic alerts.
- Record later hypothetical $5 outcomes, fees/slippage, actual bid/ask data and category-specific historical results before ever claiming successful sport copying.
- No schedule can guarantee a source trade on demand, or prove Telegram phone notifications without the actual receiving client.

**No automatic trading. No VPS. No paid provider. No crypto other than BTC in the live sports_btc feed.**
