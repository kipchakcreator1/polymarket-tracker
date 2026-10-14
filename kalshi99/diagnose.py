"""One-time public Kalshi API sample diagnostics; no auth, no orders."""
from datetime import datetime, timezone
from time import time
from kalshi99.scanner import get_json

now = int(time())
queries = {
    "open": {"status": "open", "limit": 10},
    "close_window": {"min_close_ts": now + 600, "max_close_ts": now + 7 * 86400, "limit": 10},
    "upper_only": {"max_close_ts": now + 7 * 86400, "limit": 10},
}
for name, params in queries.items():
    try:
        data = get_json("/markets", params)
        ms = data.get("markets", [])
        print(name, "num=", len(ms), "cursor=", bool(data.get("cursor")))
        print("samples=", [
            {"ticker": m.get("ticker"), "status": m.get("status"),
             "close_time": m.get("close_time"), "yes_ask": m.get("yes_ask_dollars"),
             "no_ask": m.get("no_ask_dollars")} for m in ms[:3]
        ])
    except Exception as exc:
        print(name, "ERROR", type(exc).__name__, str(exc)[:200])
