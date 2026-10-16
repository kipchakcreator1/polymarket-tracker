# 🐋 Polymarket Whale Tracker v2

> **Actual production status (2026-10-16):** the original `tracker.yml` v3.1
> and v3.2 Telegram schedules are PAUSED at the user's request. The active
> experimental alert bot is **v3.3**: its schedule lives on `main` at
> `.github/workflows/v33-telegram.yml`, while its Python implementation,
> 250-wallet shortlist and dedupe state live on `v3.3-wallet-research`.
> GitHub Actions scheduling is **best effort**, not guaranteed 24/7 real time.
> Public-data BUY alerts are research only; profitability and Telegram client
> notification display are **not guaranteed**. No paid servers or real orders.
> The legacy v2 instructions below describe the original tracker, not v3.3.


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

Track large ("whale") trades on Polymarket, follow *smart money*, detect volume
*spikes*, store trade history, and get **automatic alerts on Telegram/Discord** —
running 24/7 via GitHub Actions, no PC required.

> **100% read-only & legit.** This tool only reads public data from the
> Polymarket Data API (`data-api.polymarket.com`). It never touches or modifies
> anyone's transactions. **Not financial advice — always DYOR.**

## ✨ Features
1. 🔔 **Telegram/Discord alerts** — real-time notifications for every signal.
2. ⏰ **GitHub Actions scheduler** — runs automatically every 10 minutes in the cloud, for free.
3. 🧠 **Smart-money filter** — only alert on wallets with strong PnL & win-rate.
4. 📈 **Spike/momentum detection** — alert when a market suddenly heats up in volume.
5. 💾 **History database (SQLite)** — every trade recorded for analysis/backtesting.
6. ⭐ **Watchlist & PnL tracking** — favorite wallets list + performance summary.
7. 🎯 **Smart-money consensus** — strongest signal: alert when several whales buy the *same side* of the same market.
8. 📊 **Daily digest** — summary of top trades, hottest markets, and top whales, sent to Telegram/Discord.

## 🚀 Quick start
Requires **Python 3** only. **No dependencies to install** (uses Python's standard library).

```bash
git clone https://github.com/0xsteve-00/polymarket-tracker.git
cd polymarket-tracker

# Scan recent trades, flag whales >= $1000
python3 whale_tracker.py scan --min-usd 1000

# Real-time monitor + smart-money alerts (if alert env vars are set)
python3 whale_tracker.py watch --min-usd 5000 --smart --min-pnl 5000 --min-winrate 0.55

# Smart-money score for a wallet
python3 whale_tracker.py score 0xWALLET

# Watchlist
python3 whale_tracker.py watchlist add 0xWALLET --label "OG trader"
python3 whale_tracker.py watchlist pnl

# Markets where >=3 whales bought the same side in the last hour
python3 whale_tracker.py consensus --wallets 3 --min-usd 1000 --window 60

# 24h summary (add --send to push it to Telegram/Discord)
python3 whale_tracker.py digest --hours 24 --send
```

## 🔔 Alert setup (Telegram)
1. Chat with **@BotFather** on Telegram → `/newbot` → get your **bot token**.
2. Get your **chat id** (message **@userinfobot** or **@RawDataBot**).
3. Set env vars (local):
   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   export TELEGRAM_CHAT_ID="123456789"
   ```
   (Discord optional: create a channel webhook → set `DISCORD_WEBHOOK_URL`.)
4. Test it: `python3 whale_tracker.py poll --min-usd 2000`

See `.env.example` for a template.

## ⏰ Run 24/7 (GitHub Actions)
The workflow is ready at `.github/workflows/tracker.yml` (runs every 10 minutes).

Just set **Secrets** in the repo (`Settings → Secrets and variables → Actions`):
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `DISCORD_WEBHOOK_URL` *(optional)*

That's it — Actions will poll Polymarket every 10 minutes and send alerts to you.
The DB (`tracker.db`) is cached between runs to avoid duplicate alerts and to
enable spike detection. You can also trigger it manually from the **Actions →
Run workflow** tab.

## 🧠 How the filters work
- **whale**: `--min-usd` → trades with USD value (= shares × price) above the threshold.
- **smart**: `--smart` + `--min-pnl`/`--min-winrate`/`--min-closed` → only wallets
  whose historical realized PnL & win-rate pass. Scores are cached in the DB (saves API calls).
- **watchlist**: every wallet on the watchlist is always alerted, regardless of size.
- **spike**: `--spike-usd` + `--spike-window` (minutes) → one alert per market when
  total volume in the window crosses the threshold. Deduped per window, no spam.
- **consensus**: `--consensus-wallets N` (in `poll`) → alert when N+ distinct wallets
  buy the same outcome of one market within `--consensus-window` minutes. Several
  independent whales agreeing on one side is the strongest signal this tool tracks.

## 🗂️ Structure
```
whale_tracker.py     # main CLI (scan/watch/poll/wallet/leaderboard/score/watchlist)
polymarket_api.py    # read-only Polymarket Data API client
db.py                # SQLite (trades, alerts, scores, watchlist)
notifier.py          # Telegram + Discord
smartmoney.py        # wallet scoring (PnL & win-rate)
.github/workflows/tracker.yml   # scheduler
```

## 🤝 Contributing
This is an open-source project — issues and PRs are welcome!
See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Good first ideas:
new notification channels, better scoring, category filters, CSV export.

## 📄 License
[MIT](LICENSE) — free to use, modify, and distribute.

## ⚠️ Disclaimer
This tool is for research & informational purposes only. Trading prediction
markets is risky — all decisions and risks are your own responsibility. A big
whale entering a market is a sentiment signal, **not** a guarantee of winning.
