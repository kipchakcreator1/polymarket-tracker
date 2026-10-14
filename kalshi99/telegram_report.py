#!/usr/bin/env python3
"""Send PAPER-ONLY Kalshi portfolio events through the EXISTING Tracker 3.2 bot.

- Never imports authenticated Kalshi code or places trades.
- No token or chat id is ever printed.
- Stores acknowledgments on the isolated research branch for best-effort dedup.
- Sends new paper entries, official settled results and a once/day summary.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

DEFAULT_STATE = "research/kalshi-99c-state.json"
DEFAULT_NOTIFICATIONS = "research/kalshi-99c-telegram-state.json"
CHAT_ID_RE = re.compile(r"^-?[0-9]{4,20}$|^@[a-zA-Z][a-zA-Z0-9_]{4,31}$")


def validated_destination(env: dict) -> tuple[str, str]:
    old_bot = (env.get("TELEGRAM_V31_BOT_TOKEN") or "").strip()
    new_bot = (env.get("TELEGRAM_V32_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not old_bot or not new_bot or old_bot == new_bot:
        raise ValueError("Separate Telegram 3.2 bot configuration invalid or missing")
    if not CHAT_ID_RE.fullmatch(chat):
        raise ValueError("Telegram destination is missing or invalid")
    return new_bot, chat


def usd(amount) -> str:
    return f"${D(str(amount)):.2f}"


def event_key(pos: dict, kind: str) -> str:
    return "|".join((
        kind, str(pos.get("ticker", "")), str(pos.get("side", "")),
        str(pos.get("entry_time", ""))
    ))


def entry_text(pos: dict, cash: str) -> str:
    qty = int(pos["qty"])
    price = D(str(pos["price"]))
    cost = D(str(pos["cost_usd"]))
    profit = D(qty) - cost
    return (
        "🧪 KALSHI 99¢ — VIRTUAL BUY (NOT REAL)\n"
        f"🎯 {pos['title']}\n"
        f"📋 {pos['ticker']} · {pos['side'].upper()}\n"
        f"💰 {qty} contracts × ${price:.4f} · estimated total cost {usd(cost)}\n"
        f"🧾 Estimated commission {usd(pos.get('fee_estimated_usd', '0'))}\n"
        f"✅ Win: {usd(profit)} profit | ❌ Loss: −{usd(cost)}\n"
        f"💼 Virtual free cash: {usd(cash)}\n"
        "⚠️ Price-only test; no independent forecast. No real orders."
    )


def settlement_text(pos: dict, cash: str) -> str:
    won = bool(pos["won"])
    pnl = D(str(pos["realized_pnl_usd"]))
    return (
        f"🧪 KALSHI 99¢ — PAPER {'WIN ✅' if won else 'LOSS ❌'}\n"
        f"🎯 {pos['title']}\n"
        f"📋 {pos['ticker']} · paper {pos['side'].upper()}\n"
        f"🏁 Official result: {str(pos['result']).upper()}\n"
        f"📊 Estimated net P/L: {'+' if pnl > 0 else ''}{usd(pnl)}\n"
        f"💼 Virtual free cash: {usd(cash)}\n"
        "No real trades, deposits or funds involved."
    )


def daily_text(state: dict) -> str:
    cash = D(str(state["cash_usd"]))
    committed = sum((D(str(p["cost_usd"])) for p in state["positions"]), D("0"))
    realized = sum((D(str(h["realized_pnl_usd"])) for h in state["history"]), D("0"))
    wins = sum(bool(h.get("won")) for h in state["history"])
    losses = len(state["history"]) - wins
    return (
        "🧪 KALSHI 99¢ — DAILY PAPER REPORT\n"
        f"💵 Virtual free cash: {usd(cash)}\n"
        f"🔒 Committed to open positions: {usd(committed)}\n"
        f"📍 Open paper positions: {len(state['positions'])}\n"
        f"✅/❌ Settled W/L: {wins}/{losses}\n"
        f"📊 Realized paper P/L: {'+' if realized > 0 else ''}{usd(realized)}\n"
        "⚠️ Open-position cost is not guaranteed current value. No real funds."
    )


def notifications_due(state: dict, ack: dict, now: datetime) -> list[tuple[str, str]]:
    """Return stable IDs. One entry and one result per unique hypothetical bet."""
    if state.get("mode") != "PAPER_ONLY":
        raise ValueError("Only PAPER_ONLY portfolio is supported")
    already = set(ack.get("sent_ids", []))
    cash = state["cash_usd"]
    due = []
    # Historical fills may never have had an entry notification after setup.
    for pos in state["history"]:
        eid = event_key(pos, "buy")
        if eid not in already:
            due.append((eid, entry_text(pos, cash)))
        sid = event_key(pos, "settled")
        if sid not in already:
            due.append((sid, settlement_text(pos, cash)))
    for pos in state["positions"]:
        eid = event_key(pos, "buy")
        if eid not in already:
            due.append((eid, entry_text(pos, cash)))
    # Daily snapshot once after 15:00 UTC, even if no new trades.
    if now.hour >= 15:
        did = "daily|" + now.strftime("%Y-%m-%d")
        if did not in already:
            due.append((did, daily_text(state)))
    return due


def send_message(token: str, chat: str, msg: str) -> None:
    """Telegram-only POST; never put the URL with the secret in an exception."""
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    data = json.dumps({"chat_id": chat, "text": msg, "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
            if not payload.get("ok", False):
                raise RuntimeError("Telegram rejected paper report")
    except urllib.error.HTTPError as exc:
        # HTTPError.__str__ includes the bot token in the request URL.
        raise RuntimeError(f"Telegram HTTP status {exc.code} (check bot Start and secrets)") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Telegram notification network error; retry later") from None


def run(state_path: str, ack_path: str, env=None, now=None, sender=send_message) -> int:
    env = env if env is not None else os.environ
    now = now or datetime.now(timezone.utc)
    token, chat = validated_destination(env)  # fail before reading/writing files
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    path = Path(ack_path)
    ack = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": 1, "sent_ids": []}
    if ack.get("schema") != 1 or not isinstance(ack.get("sent_ids"), list):
        raise ValueError("Unexpected Telegram notification checkpoint")
    pending = notifications_due(state, ack, now)
    count = 0
    for eid, message in pending:
        sender(token, chat, message)
        # Persist local ACK after every *confirmed* Telegram response. Workflow
        # later commits this file. A crash between send and commit can repeat a
        # message; financial state is independent and never altered by sending.
        ack["sent_ids"].append(eid)
        ack["sent_ids"] = ack["sent_ids"][-20000:]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(ack, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        count += 1
    print(f"Kalshi PAPER Telegram: sent_messages={count}; cash_and_trades_unchanged")
    return count


def main():
    parser = argparse.ArgumentParser(description="Send Kalshi paper updates via separate Tracker 3.2 Telegram bot")
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--checkpoint", default=DEFAULT_NOTIFICATIONS)
    args = parser.parse_args()
    try:
        run(args.state, args.checkpoint)
    except Exception as exc:
        # Scripts used in automation only produce scrubbed, static error text;
        # never print repr of URL exceptions or environment variables.
        print("Kalshi PAPER Telegram error:", type(exc).__name__, str(exc)[:180])
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
