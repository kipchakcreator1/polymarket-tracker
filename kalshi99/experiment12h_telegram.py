"""Telegram notifications for the 12-hour PAPER-ONLY Kalshi experiment.

Use existing 3.2 bot; distinguish observed quotations, hypothetical entries,
and official settlement. No trade keys, no authenticated Kalshi endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from kalshi99 import experiment12h as ex
from kalshi99 import telegram_report as tg

ACK_FILE = "research/kalshi-12h-telegram.json"
MAX_QUOTE_ALERTS = 36


def read_ack(path):
    p = Path(path)
    ack = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"schema": 1, "sent": []}
    if ack.get("schema") != 1 or not isinstance(ack.get("sent"), list):
        raise ValueError("Bad Kalshi 12h notification checkpoint")
    return ack


def status_message(state, now, title):
    parts = [
        "🧪 KALSHI 12H — " + title,
        "🕒 " + now.strftime("%d.%m %H:%M UTC") + " | конец " + ex.END.strftime("%H:%M UTC"),
        "🔎 Сканов: " + str(state["scans"]),
        "📈 Проверенных ask: " + str(state["total_verified_quotes"])
        + " (уникальных " + str(len(state["unique_verified_market_sides"])) + ")",
    ]
    for key, tag in (("taker_12h", "До 12ч"), ("taker_near_1h", "До 1ч")):
        p = ex.portfolio_stats(state["portfolios"][key])
        parts.append(
            "💼 " + tag + ": свободно $" + p["cash_usd"] +
            ", открыто " + str(p["open"]) + ", W/L " +
            str(p["wins"]) + "/" + str(p["losses"]) +
            ", реализованный P/L $" + p["realized_pnl_usd"]
        )
    parts.append("⚠️ PAPER ONLY. Цена 99¢ не гарантирует победу. Реальных ставок нет.")
    return "\n".join(parts)


def buy_message(strategy, p):
    qty = int(p["qty"])
    profit = qty - float(p["cost_usd"])
    return (
        "🧪 KALSHI 12H — ВИРТУАЛЬНАЯ ПОКУПКА\n"
        "📍 " + strategy + "\n🎯 " + p["title"] +
        "\n📋 " + p["ticker"] + " · " + p["side"].upper() +
        "\n💵 " + str(qty) + " шт. по " + p["price"] +
        " | общая цена $" + p["cost_usd"] +
        "\n✅ Если победа: +$" + format(profit, ".2f") +
        " | ❌ проигрыш: −$" + p["cost_usd"] +
        "\nНе реальная сделка. Paper fill по моментальному снимку ask."
    )


def settled_message(strategy, p):
    pnl = float(p["realized_pnl_usd"])
    pnl_display = ("+$" if pnl > 0 else "−$" if pnl < 0 else "$") + format(abs(pnl), ".2f")
    return (
        "🧪 KALSHI 12H — " + ("PAPER WIN ✅" if p["won"] else "PAPER LOSS ❌") +
        "\n📍 " + strategy + "\n🎯 " + p["title"] +
        "\n🏁 Результат: " + p["result"].upper() +
        "\n💰 P/L: " + pnl_display + "\nВиртуальные деньги."
    )


def quote_message(q):
    return (
        "🔎 KALSHI 12H — ИНТЕРЕСНАЯ КОТИРОВКА, НЕ ПОКУПКА\n"
        "🎯 " + q["title"] +
        "\n📋 " + q["ticker"] + " · " + q["side"].upper() +
        "\n💵 Ask " + str(q["sampled_ask"]) +
        " · доступно " + str(q["available_at_best_ask"]) + " контрактов" +
        "\n⏱ До закрытия: " + str(q["hours_to_close"]) + " ч" +
        "\n💰 Виртуально " + str(q["hypothetical_qty"]) +
        " шт.: выигрыш +$" + str(q["win_profit_taker_usd"]) +
        ", проигрыш −$" + str(q["loss_taker_usd"]) +
        "\nЦена и исполнение могут измениться. Вероятность не оценена."
    )


def due_messages(state, ack, now, latest=None):
    if state.get("mode") != "PAPER_ONLY_12H":
        raise ValueError("Only PAPER_ONLY_12H experiment is supported")
    seen = set(ack["sent"])
    events = []
    if "initial" not in seen:
        events.append(("initial", status_message(state, now, "ПОДКЛЮЧЕНО К TELEGRAM")))
    for name, portfolio in state["portfolios"].items():
        if portfolio.get("mode") != "PAPER_ONLY":
            raise ValueError("Real-money portfolio rejected")
        for pos in portfolio["history"]:
            buy = "buy|" + name + "|" + tg.event_key(pos, "buy")
            settled = "settled|" + name + "|" + tg.event_key(pos, "settled")
            if buy not in seen:
                events.append((buy, buy_message(name, pos)))
            if settled not in seen:
                events.append((settled, settled_message(name, pos)))
        for pos in portfolio["positions"]:
            buy = "buy|" + name + "|" + tg.event_key(pos, "buy")
            if buy not in seen:
                events.append((buy, buy_message(name, pos)))
    if latest and ex.START <= now < ex.END:
        remaining = MAX_QUOTE_ALERTS - sum(k.startswith("quote|") for k in seen)
        if remaining > 0:
            count = 0
            for q in latest.get("quotes", []):
                if float(q.get("hours_to_close", 999)) > 1:
                    continue
                key = "quote|" + q["ticker"] + "|" + q["side"]
                if key not in seen:
                    events.append((key, quote_message(q)))
                    count += 1
                    if count >= min(2, remaining):
                        break
    if ex.START <= now < ex.END:
        hour_key = "hour|" + now.strftime("%Y%m%d%H")
        if hour_key not in seen and "initial" in seen:
            events.append((hour_key, status_message(state, now, "ПОЧАСОВОЙ ОТЧЁТ")))
    if now >= ex.END and "final" not in seen:
        events.append(("final", status_message(state, now, "12 ЧАСОВ ЗАВЕРШЕНЫ")))
    return events


def run(state_path=ex.DEFAULT_STATE, snapshot_path=ex.DEFAULT_SNAPSHOTS,
        ack_path=ACK_FILE, now=None, env=None, sender=None):
    env = env if env is not None else os.environ
    token, chat = tg.validated_destination(env)
    sender = sender if sender is not None else tg.send_message
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = ex.load_state(state_path)
    ack = read_ack(ack_path)
    latest = None
    p = Path(snapshot_path)
    if p.exists():
        rows = p.read_text(encoding="utf-8").splitlines()
        if rows:
            item = json.loads(rows[-1])
            stamped = datetime.fromisoformat(item["sampled_at_utc"])
            if 0 <= (now - stamped).total_seconds() <= 12 * 60:
                latest = item
    due = due_messages(state, ack, now, latest)
    for key, message in due:
        sender(token, chat, message)
        ack["sent"].append(key)
        dest = Path(ack_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_name(dest.name + ".tmp")
        temp.write_text(json.dumps(ack, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
        os.replace(temp, dest)
    print("Kalshi 12h PAPER Telegram: sent", len(due), "messages")
    return len(due)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=ex.DEFAULT_STATE)
    parser.add_argument("--snapshots", default=ex.DEFAULT_SNAPSHOTS)
    parser.add_argument("--ack", default=ACK_FILE)
    args = parser.parse_args()
    try:
        run(args.state, args.snapshots, args.ack)
    except Exception as exc:
        # DO NOT log Telegram request URLs, environment values, bot tokens.
        print("Kalshi 12h Telegram error:", type(exc).__name__,
              str(exc)[:100] if isinstance(exc, ValueError) else "delivery unsuccessful")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
