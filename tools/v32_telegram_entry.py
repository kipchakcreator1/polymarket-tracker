#!/usr/bin/env python3
"""Run Tracker 3.2 with a DISTINCT bot token, allowed to use the same DM chat ID.

For direct messages, Telegram chat_id is the human user ID and is normally the
same across multiple bot accounts. Separation comes from the bot token, NOT
from a different chat ID. Fail closed before any trades API or Telegram call
if missing secrets, an invalid destination, or reused production bot token.
Never print tokens or destination IDs.
"""
import os
import re
import sys
from pathlib import Path

TELEGRAM_CHAT_ID_RE = re.compile(r"^-?[0-9]{4,20}$|^@[a-zA-Z][a-zA-Z0-9_]{4,31}$")


def validate_route(env):
    old_token = (env.get("TELEGRAM_V31_BOT_TOKEN") or "").strip()
    new_token = (env.get("TELEGRAM_V32_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not old_token:
        raise ValueError("Original 3.1 bot token not configured")
    if not new_token:
        raise ValueError("New 3.2 bot token not configured")
    if old_token == new_token:
        raise ValueError("3.2 bot token matches the original 3.1 bot; abort")
    if not chat_id or not TELEGRAM_CHAT_ID_RE.fullmatch(chat_id):
        raise ValueError("Telegram destination missing or invalid")
    return new_token, chat_id


def run():
    # Unlike the old proposal, private chat IDs can legitimately be equal.
    token, chat_id = validate_route(os.environ)
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
    os.environ["V32_TELEGRAM_ALERT_LABEL"] = "1"
    print("V3.2 TELEGRAM: separate bot validated; no real orders")
    # GitHub launches this ENTRY FILE from tools/, so Python puts tools/ rather
    # than the repository root on sys.path. Preserve fail-closed token routing
    # before exposing the existing read-only tracker package to imports.
    repository_root = str(Path(__file__).resolve().parent.parent)
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    import smart_tracker_v3
    smart_tracker_v3.main()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # No values from env or tokens are ever included in error messages.
        print("V3.2 TELEGRAM STARTUP ERROR:", type(exc).__name__,
              str(exc)[:120], file=sys.stderr)
        sys.exit(1)
