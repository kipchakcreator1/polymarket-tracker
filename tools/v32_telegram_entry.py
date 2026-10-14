#!/usr/bin/env python3
"""Strictly route experimental v3.2 Telegram alerts to a DIFFERENT chat.

Do not use the live 3.1 chat as a fallback. Never log the bot token or chat IDs.
This entrypoint reads the exact 3.2 secrets intended for a dedicated workflow
and fails closed before any wallet API request or Telegram call.
"""
import os
import re
import sys

TELEGRAM_CHAT_ID_RE = re.compile(r"^-?[0-9]{4,20}$|^@[a-zA-Z][a-zA-Z0-9_]{4,31}$")


def validate_route(env):
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    v31 = (env.get("TELEGRAM_V31_CHAT_ID") or "").strip()
    v32 = (env.get("TELEGRAM_V32_CHAT_ID") or "").strip()
    if not token:
        raise ValueError("Telegram bot token not configured")
    if not v31:
        raise ValueError("Existing 3.1 Telegram destination not configured")
    if not v32:
        raise ValueError("Separate 3.2 Telegram destination not configured")
    if not TELEGRAM_CHAT_ID_RE.fullmatch(v32):
        raise ValueError("3.2 destination must be numeric chat ID or @publicchannel")
    if v31 == v32:
        raise ValueError("3.2 destination matches the live 3.1 chat; abort")
    return v32


def run():
    # Failure here cannot accidentally invoke the legacy default chat.
    destination = validate_route(os.environ)
    os.environ["TELEGRAM_CHAT_ID"] = destination
    os.environ["V32_TELEGRAM_ALERT_LABEL"] = "1"
    print("V3.2 TELEGRAM: separate chat validated; trading disabled")
    import smart_tracker_v3
    smart_tracker_v3.main()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("V3.2 TELEGRAM STARTUP ERROR:", type(exc).__name__,
              str(exc)[:120], file=sys.stderr)
        sys.exit(1)
