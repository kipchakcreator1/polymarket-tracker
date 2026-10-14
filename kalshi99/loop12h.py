#!/usr/bin/env python3
"""Continuous five-minute runner for the existing 12-hour PAPER-ONLY experiment.

Runs inside one GitHub Actions job (max ~4h50), safely checkpoints after every
cycle and asks workflow_dispatch to hand off to a fresh job before the hosted
runner's 6-hour limit. The surrounding YAML handles the dispatch and dedup
concurrency. No real trades. No extension of the fixed experiment window.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from kalshi99.experiment12h import START, END, CADENCE_SECONDS

SEGMENT_SECONDS = 4 * 3600 + 50 * 60  # 290 min, safely below 6h max.
HANDOFF_RESERVE_SECONDS = 60
RESEARCH_BRANCH = "kalshi-12h-research"
SCAN_MODULE = "kalshi99.experiment12h"
TELEGRAM_MODULE = "kalshi99.experiment12h_telegram"


def next_scan_epoch(epoch: float) -> float:
    """First fixed five-minute bucket boundary after *epoch* (no duplicates)."""
    start = START.timestamp()
    if epoch < start:
        return start
    return start + (int((epoch - start) // CADENCE_SECONDS) + 1) * CADENCE_SECONDS


def decision(now: float, segment_start: float) -> str:
    """Independent of API, wall-clock sleeps and GitHub workflow state."""
    if now >= END.timestamp():
        return "finalize"
    if now >= segment_start + SEGMENT_SECONDS - HANDOFF_RESERVE_SECONDS:
        return "handoff"
    return "scan"


def cmd(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check)


def persist_checkpoint() -> None:
    # Only research/ files are committed; never touch 3.1 or 3.2 states.
    cmd("git", "add", "-A", "research/")
    diff = cmd("git", "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return
    if diff.returncode != 1:
        raise RuntimeError("Unable to inspect Git checkpoint diff")
    cmd("git", "commit", "-m", "chore: 12h continuous paper checkpoint [skip ci]")
    cmd("git", "pull", "--rebase", "origin", RESEARCH_BRANCH)
    cmd("git", "push", "origin", "HEAD:" + RESEARCH_BRANCH)


def one_pass() -> bool:
    """Try one paper scan, then notifications; always checkpoint changes.

    API failures are logged and retried on next tick; Git write failures are
    fatal so a new job does not silently overwrite uncommitted state.
    """
    now = datetime.now(timezone.utc)
    print(f"KALSHI 12H continuous tick: {now.isoformat()}", flush=True)
    scanned = False
    try:
        cmd(sys.executable, "-m", SCAN_MODULE)
        scanned = True
    except subprocess.CalledProcessError as exc:
        print(f"Paper scan failed (exit {exc.returncode}); will retry.", flush=True)
    try:
        cmd(sys.executable, "-m", TELEGRAM_MODULE)
    except subprocess.CalledProcessError as exc:
        print(f"Telegram step failed (exit {exc.returncode}); will retry.", flush=True)
    persist_checkpoint()
    return scanned


def write_handoff_flag(chain: bool) -> None:
    value = "true" if chain else "false"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fp:
            fp.write(f"chain={value}\n")
    print("KALSHI 12H continuation request:", value, flush=True)


def main() -> None:
    start = time.time()
    # A new job must not rewrite START or END from the experiment.
    print(
        f"12-hour PAPER window: {START.isoformat()} -> {END.isoformat()}. "
        f"Segment limit {SEGMENT_SECONDS // 60}m. "
        "No real trading, existing portfolio preserved.",
        flush=True,
    )
    cmd("git", "config", "user.name", "github-actions[bot]")
    cmd("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    while True:
        now = time.time()
        outcome = decision(now, start)
        if outcome == "finalize":
            one_pass()  # experiment finalizes, then Telegram sends final report
            write_handoff_flag(False)
            return
        if outcome == "handoff":
            write_handoff_flag(True)
            return
        one_pass()
        # For the initial workflow push only: verify automatic GitHub
        # workflow_dispatch continuation promptly, not five hours later.
        if os.environ.get("KALSHI_INITIAL_CHAIN_PROBE") == "1":
            write_handoff_flag(True)
            return
        now = time.time()
        target = min(next_scan_epoch(now) + 4, END.timestamp())
        segment_last = start + SEGMENT_SECONDS - HANDOFF_RESERVE_SECONDS
        pause = min(target, segment_last) - now
        if pause > 0:
            print(
                f"Next scan in ~{int(pause)}s; existing GitHub runner stays active.",
                flush=True,
            )
            time.sleep(pause)


if __name__ == "__main__":
    main()
