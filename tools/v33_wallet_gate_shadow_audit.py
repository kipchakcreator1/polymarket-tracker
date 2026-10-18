"""Bounded read-only comparison of legacy wallet score vs v2 position evidence.

No Telegram, state mutations, trading credentials, or portfolio recommendations.
First page per status is NOT a complete economic ledger.
"""
import argparse
import json
import math
import time
from pathlib import Path

import smart_tracker_v2 as api

SOURCE = Path("research/v33_wallet_shortlist.json")
TARGET = Path("artifacts/v33-risk-api-audit.json")
FOCUS_PREFIXES = (
    "0x186f64ad1d",  # Seen in timestamp warning
    "0x0a7491c71e",  # Seen in timestamp warning
    "0xf227388c01",  # Seen in timestamp warning
    "0x5b6331e7ff",  # Priority research control
)
STATUS_TYPES = ("CLOSED", "REDEEMABLE_LOST")
PAGE_LIMIT = 250  # one bounded real API page; no pagination claims


def timestamp_check(raw, now):
    """Diagnostic only: never repair an unsupported raw unit by guessing."""
    if raw is None or isinstance(raw, bool):
        return {"state": "missing", "raw": repr(raw)[:50]}
    try:
        number = float(raw)
    except (ValueError, TypeError, OverflowError):
        return {"state": "unsupported_format", "raw": repr(raw)[:50]}
    if not math.isfinite(number):
        return {"state": "nonfinite", "raw": repr(raw)[:50]}
    if number >= 1e14:  # microseconds/nanoseconds: not in documented input contract
        return {"state": "unsupported_large_unit", "raw": repr(raw)[:50]}
    unit = "ms" if number >= 1e12 else "seconds"
    seconds = number / 1000 if unit == "ms" else number
    if seconds <= 0:
        state = "nonpositive"
    elif seconds > now + 120:
        state = "future"
    else:
        state = "valid"
    return {"state": state, "unit": unit, "seconds": int(seconds),
            "raw": repr(raw)[:50]}


def position_page(rows, status, now):
    if not isinstance(rows, list):
        raise ValueError("Non-list position page")
    state_counts = {}
    invalid_times = []
    timestamp_states = {}
    pnl_rows = 0
    negative_pnl = 0
    major_losses = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed position row")
        kind = str(row.get("status"))
        state_counts[kind] = state_counts.get(kind, 0) + 1
        ts = timestamp_check(row.get("last_event_at"), now)
        timestamp_states[ts["state"]] = timestamp_states.get(ts["state"], 0) + 1
        if ts["state"] != "valid" and len(invalid_times) < 5:
            invalid_times.append(ts)
        if status == "REDEEMABLE_LOST" and row.get("status") == "REDEEMABLE" and row.get("redeemable") is True:
            try:
                realized = float(row["realized_pnl"])
                unrealized = float(row["unrealized_pnl"])
                pnl = realized + unrealized
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            if math.isfinite(pnl):
                pnl_rows += 1
                negative_pnl += int(pnl < -.01)
                major_losses += int(pnl <= -100)
    return {"rows": len(rows), "status_values": state_counts,
            "timestamp_states": timestamp_states,
            "invalid_timestamps_sample": invalid_times,
            "pnl_rows_with_observed_numeric_values": pnl_rows,
            "redeemable_negative_rows": negative_pnl,
            "redeemable_major_loss_rows": major_losses}


def audit_one(wallet, legacy, now, fetch):
    result = {"wallet_prefix": wallet[:14],
              "legacy_snapshot": {
                  "closed_sample": legacy.get("closed"),
                  "positive_fraction": legacy.get("profitable_closed_rate"),
                  "profit_factor": legacy.get("profit_factor"),
                  "realized_closed_pnl": legacy.get("realized_closed_pnl")},
              "full_economic_ledger_verified": False,
              "usable_for_promoting_wallet": False,
              "pages": {}}
    for status in STATUS_TYPES:
        params = {"user": wallet, "status": status, "limit": PAGE_LIMIT,
                  "sort_by": "TIMESTAMP", "sort_direction": "DESC"}
        if status == "REDEEMABLE_LOST":
            params["include_archived"] = "true"
        try:
            response = fetch("/v2/positions", params, tries=2, timeout=12)
            if not (isinstance(response, dict) and
                    isinstance(response.get("data"), list) and
                    isinstance(response.get("pagination"), dict) and
                    isinstance(response["pagination"].get("has_more"), bool)):
                raise ValueError("Malformed position envelope or pagination")
            page = position_page(response["data"], status, now)
            page["pagination_complete"] = response["pagination"]["has_more"] is False
            page["not_a_full_ledger"] = True
            result["pages"][status] = page
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            result["pages"][status] = {
                "api_or_schema_error": type(exc).__name__,
                "detail": str(exc)[:170],
                "pagination_complete": False,
                "not_a_full_ledger": True}
    return result


def choose_wallets(shortlist):
    entries = shortlist.get("shortlist")
    if not isinstance(entries, list):
        raise ValueError("Invalid shortlist")
    chosen = []
    for prefix in FOCUS_PREFIXES:
        item = next((w for w in entries
                     if isinstance(w, dict) and
                     str(w.get("wallet", "")).startswith(prefix)), None)
        if item is not None:
            chosen.append(item)
    return chosen


def run(shortlist, now, fetch):
    sample = choose_wallets(shortlist)
    entries = [audit_one(str(x["wallet"]), x, now, fetch) for x in sample]
    return {"schema": "v33_risk_shadow_audit_1", "captured_at": now,
            "samples": entries, "sample_size": len(entries),
            "disclaimer": ("Read-only first-page comparison, NOT a complete PnL ledger, "
                           "no wallet promotion, no signals, no trading.")}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--live", action="store_true",
                     help="Explicitly fetch a bounded set of public v2 position pages")
    args = cli.parse_args()
    if not args.live:
        raise SystemExit("Use --live for explicit read-only public API audit")
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    report = run(data, int(time.time()), api.request_json)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    print("READ-ONLY: no Telegram, no orders, no production state changes")


if __name__ == "__main__":
    main()
