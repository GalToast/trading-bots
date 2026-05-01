#!/usr/bin/env python3
"""
SYNC_CLOSE Attribution Report
=============================
Reads trade_behavior_log.jsonl, filters for SYNC_CLOSE exits, and produces
an aggregated breakdown to answer:
  - WHY does SYNC_CLOSE fire?
  - Which code paths are the biggest culprits?

Usage:
    python scripts/sync_close_attribution.py
    python scripts/sync_close_attribution.py --jsonl /path/to/trade_behavior_log.jsonl
    python scripts/sync_close_attribution.py --recent 50
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def _fmt_pnl(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:.2f}"


def _fmt_hold(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.2f}h"


def _is_rich_positive(value: float) -> str:
    """Color helper for rich output."""
    return _fmt_pnl(value)


# ---------------------------------------------------------------------------
# Trigger source inference
# ---------------------------------------------------------------------------

def classify_trigger(trade: dict[str, Any]) -> str:
    """Infer the code path that triggered SYNC_CLOSE.

    Heuristics (ordered by priority):
    - adopted_reload  : adopted=True AND entry_context contains 'reloaded'
    - adopt_snapshot  : adopted=True AND entry_context does NOT contain 'reloaded'
    - instant_close   : hold_seconds == 0 and NOT adopted  (sync loop closes immediately)
    - short_hold      : hold_seconds < 60 and NOT adopted  (sync loop catches stale)
    - cleanup_hold    : hold_seconds >= 60                 (cleanup/shutdown window)
    """
    adopted = trade.get("adopted", False)
    context = str(trade.get("entry_context", ""))
    hold = trade.get("hold_seconds")

    if adopted and "reloaded" in context:
        return "adopted_reload"
    if adopted:
        return "adopt_snapshot"
    if hold is not None and hold == 0:
        return "instant_close"
    if hold is not None and hold < 60:
        return "short_hold"
    return "cleanup_hold"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_sync_trades(jsonl_path: str) -> list[dict[str, Any]]:
    """Load all SYNC_CLOSE trades from the JSONL file."""
    trades: list[dict[str, Any]] = []
    skipped = 0

    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                continue

            exit_reason = str(rec.get("exit_reason", ""))
            exit_type = str(rec.get("exit_type", "")).lower()

            if "SYNC_CLOSE" in exit_reason or exit_type == "sync_close":
                rec["_trigger"] = classify_trigger(rec)
                trades.append(rec)

    return trades, skipped


# ---------------------------------------------------------------------------
# Plain-text output
# ---------------------------------------------------------------------------

def print_plain(trades: list[dict], skipped: int, recent_n: int = 20) -> None:
    w = sys.stdout.write

    w("=" * 80 + "\n")
    w("  SYNC_CLOSE Attribution Report\n")
    w("=" * 80 + "\n\n")

    if skipped:
        w(f"  [WARN] Skipped {skipped} malformed line(s).\n\n")

    if not trades:
        w("  No SYNC_CLOSE trades found.\n")
        return

    # -- Totals --
    total_pnl = sum(t.get("realized_pnl", 0) for t in trades)
    wins = sum(1 for t in trades if (t.get("realized_pnl", 0) or 0) > 0)

    w(f"  Total SYNC_CLOSE trades : {len(trades)}\n")
    w(f"  Total P&L               : {_fmt_pnl(total_pnl)}\n")
    w(f"  Win rate                : {_fmt_pct(wins / len(trades) * 100)} ({wins}W / {len(trades) - wins}L)\n")
    w(f"  Avg hold time           : {_fmt_hold(sum(t.get('hold_seconds', 0) or 0 for t in trades) / len(trades))}\n")
    w(f"  Avg P&L per trade       : {_fmt_pnl(total_pnl / len(trades))}\n\n")

    # -- By symbol --
    by_sym: dict[str, list] = defaultdict(list)
    for t in trades:
        by_sym[t.get("symbol", "UNKNOWN")].append(t)

    w("  --- By Symbol ---\n")
    w(f"  {'Symbol':<12} {'Count':>6} {'Total P&L':>12} {'Avg P&L':>10} {'Win Rate':>10} {'Avg Hold':>10}\n")
    w("  " + "-" * 62 + "\n")
    for sym in sorted(by_sym, key=lambda s: sum(t.get("realized_pnl", 0) for t in by_sym[s]), reverse=True):
        subset = by_sym[sym]
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        ah = sum(t.get("hold_seconds", 0) or 0 for t in subset) / len(subset)
        w(f"  {sym:<12} {len(subset):>6} {_fmt_pnl(pnl):>12} {_fmt_pnl(avg):>10} {_fmt_pct(wr):>10} {_fmt_hold(ah):>10}\n")
    w("\n")

    # -- By mode --
    by_mode: dict[str, list] = defaultdict(list)
    for t in trades:
        by_mode[t.get("entry_mode", "UNKNOWN")].append(t)

    w("  --- By Mode ---\n")
    w(f"  {'Mode':<16} {'Count':>6} {'Total P&L':>12} {'Avg P&L':>10} {'Win Rate':>10}\n")
    w("  " + "-" * 56 + "\n")
    for mode in sorted(by_mode, key=lambda m: sum(t.get("realized_pnl", 0) for t in by_mode[m]), reverse=True):
        subset = by_mode[mode]
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        w(f"  {mode:<16} {len(subset):>6} {_fmt_pnl(pnl):>12} {_fmt_pnl(avg):>10} {_fmt_pct(wr):>10}\n")
    w("\n")

    # -- By trigger source --
    by_trigger: dict[str, list] = defaultdict(list)
    for t in trades:
        by_trigger[t.get("_trigger", "unknown")].append(t)

    w("  --- By Trigger Source ---\n")
    w(f"  {'Trigger':<20} {'Count':>6} {'Total P&L':>12} {'Avg P&L':>10} {'Win Rate':>10} {'Avg Hold':>10}\n")
    w("  " + "-" * 70 + "\n")
    for trig in sorted(by_trigger, key=lambda k: len(by_trigger[k]), reverse=True):
        subset = by_trigger[trig]
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        ah = sum(t.get("hold_seconds", 0) or 0 for t in subset) / len(subset)
        w(f"  {trig:<20} {len(subset):>6} {_fmt_pnl(pnl):>12} {_fmt_pnl(avg):>10} {_fmt_pct(wr):>10} {_fmt_hold(ah):>10}\n")
    w("\n")

    # -- Adopted vs Direct --
    adopted = [t for t in trades if t.get("adopted")]
    direct = [t for t in trades if not t.get("adopted")]
    w("  --- Adopted vs Direct ---\n")
    for label, subset in [("Adopted", adopted), ("Direct", direct)]:
        if not subset:
            continue
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        ah = sum(t.get("hold_seconds", 0) or 0 for t in subset) / len(subset)
        w(f"  {label:<12} {len(subset):>6} {_fmt_pnl(pnl):>12} {_fmt_pnl(avg):>10} {_fmt_pct(wr):>10} {_fmt_hold(ah):>10}\n")
    w("\n")

    # -- Worst 10 --
    worst = sorted(trades, key=lambda t: t.get("realized_pnl", 0))[:10]
    w("  --- Top 10 Worst SYNC_CLOSE Trades ---\n")
    w(f"  {'#':>3}  {'Symbol':<12} {'Mode':<14} {'P&L':>10} {'Hold':>8} {'Trigger':<20} {'Exit Reason'}\n")
    w("  " + "-" * 82 + "\n")
    for i, t in enumerate(worst, 1):
        w(f"  {i:>3}  {t.get('symbol','?'):<12} {t.get('entry_mode','?'):<14} {_fmt_pnl(t.get('realized_pnl',0)):>10} {_fmt_hold(t.get('hold_seconds')):>8} {t.get('_trigger','?'):<20} {t.get('exit_reason','')}\n")
    w("\n")

    # -- Recent N --
    recent = trades[-recent_n:]
    w(f"  --- Recent {len(recent)} SYNC_CLOSE Trades ---\n")
    w(f"  {'Symbol':<12} {'Mode':<14} {'P&L':>10} {'Hold':>8} {'Trigger':<20} {'Adopted':>8} {'Exit Time'}\n")
    w("  " + "-" * 86 + "\n")
    for t in recent:
        adopted_str = "YES" if t.get("adopted") else "no"
        exit_time = t.get("exit_time_utc", "?")
        w(f"  {t.get('symbol','?'):<12} {t.get('entry_mode','?'):<14} {_fmt_pnl(t.get('realized_pnl',0)):>10} {_fmt_hold(t.get('hold_seconds')):>8} {t.get('_trigger','?'):<20} {adopted_str:>8} {exit_time}\n")

    w("\n" + "=" * 80 + "\n")


# ---------------------------------------------------------------------------
# Rich output
# ---------------------------------------------------------------------------

def print_rich(trades: list[dict], skipped: int, recent_n: int = 20) -> None:
    console = Console()

    console.print(Panel.fit("[bold cyan]SYNC_CLOSE Attribution Report[/bold cyan]"))

    if skipped:
        console.print(f"[yellow]Skipped {skipped} malformed line(s).[/yellow]")

    if not trades:
        console.print("[red]No SYNC_CLOSE trades found.[/red]")
        return

    # -- Totals --
    total_pnl = sum(t.get("realized_pnl", 0) for t in trades)
    wins = sum(1 for t in trades if (t.get("realized_pnl", 0) or 0) > 0)
    avg_hold = sum(t.get("hold_seconds", 0) or 0 for t in trades) / len(trades)

    tbl = Table(title="Summary")
    tbl.add_column("Metric")
    tbl.add_column("Value", justify="right")
    pnl_color = "green" if total_pnl >= 0 else "red"
    tbl.add_row("Total trades", str(len(trades)))
    tbl.add_row("Total P&L", f"[{pnl_color}]{_fmt_pnl(total_pnl)}[/{pnl_color}]")
    tbl.add_row("Win rate", f"{_fmt_pct(wins / len(trades) * 100)} ({wins}W / {len(trades) - wins}L)")
    tbl.add_row("Avg hold", _fmt_hold(avg_hold))
    tbl.add_row("Avg P&L/trade", _fmt_pnl(total_pnl / len(trades)))
    console.print(tbl)
    console.print()

    # -- By symbol --
    by_sym: dict[str, list] = defaultdict(list)
    for t in trades:
        by_sym[t.get("symbol", "UNKNOWN")].append(t)

    tbl = Table(title="By Symbol")
    tbl.add_column("Symbol")
    tbl.add_column("Count", justify="right")
    tbl.add_column("Total P&L", justify="right")
    tbl.add_column("Avg P&L", justify="right")
    tbl.add_column("Win Rate", justify="right")
    tbl.add_column("Avg Hold", justify="right")
    for sym in sorted(by_sym, key=lambda s: sum(t.get("realized_pnl", 0) for t in by_sym[s]), reverse=True):
        subset = by_sym[sym]
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        ah = sum(t.get("hold_seconds", 0) or 0 for t in subset) / len(subset)
        c = "green" if pnl >= 0 else "red"
        tbl.add_row(sym, str(len(subset)), f"[{c}]{_fmt_pnl(pnl)}[/{c}]", _fmt_pnl(avg), _fmt_pct(wr), _fmt_hold(ah))
    console.print(tbl)
    console.print()

    # -- By mode --
    by_mode: dict[str, list] = defaultdict(list)
    for t in trades:
        by_mode[t.get("entry_mode", "UNKNOWN")].append(t)

    tbl = Table(title="By Mode")
    tbl.add_column("Mode")
    tbl.add_column("Count", justify="right")
    tbl.add_column("Total P&L", justify="right")
    tbl.add_column("Avg P&L", justify="right")
    tbl.add_column("Win Rate", justify="right")
    for mode in sorted(by_mode, key=lambda m: sum(t.get("realized_pnl", 0) for t in by_mode[m]), reverse=True):
        subset = by_mode[mode]
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        c = "green" if pnl >= 0 else "red"
        tbl.add_row(mode, str(len(subset)), f"[{c}]{_fmt_pnl(pnl)}[/{c}]", _fmt_pnl(avg), _fmt_pct(wr))
    console.print(tbl)
    console.print()

    # -- By trigger source --
    by_trigger: dict[str, list] = defaultdict(list)
    for t in trades:
        by_trigger[t.get("_trigger", "unknown")].append(t)

    TRIGGER_LABELS = {
        "adopted_reload": "Adopted position reloaded from prior session",
        "adopt_snapshot": "Adopted via snapshot (no reload context)",
        "instant_close": "Instant close (hold=0, sync loop caught it immediately)",
        "short_hold": "Short hold (<60s, sync loop caught stale entry)",
        "cleanup_hold": "Cleanup/shutdown window (hold >= 60s)",
    }

    tbl = Table(title="By Trigger Source")
    tbl.add_column("Trigger")
    tbl.add_column("Description", max_width=48)
    tbl.add_column("Count", justify="right")
    tbl.add_column("Total P&L", justify="right")
    tbl.add_column("Avg P&L", justify="right")
    tbl.add_column("Win Rate", justify="right")
    tbl.add_column("Avg Hold", justify="right")
    for trig in sorted(by_trigger, key=lambda k: len(by_trigger[k]), reverse=True):
        subset = by_trigger[trig]
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        ah = sum(t.get("hold_seconds", 0) or 0 for t in subset) / len(subset)
        c = "green" if pnl >= 0 else "red"
        tbl.add_row(trig, TRIGGER_LABELS.get(trig, ""), str(len(subset)),
                    f"[{c}]{_fmt_pnl(pnl)}[/{c}]", _fmt_pnl(avg), _fmt_pct(wr), _fmt_hold(ah))
    console.print(tbl)
    console.print()

    # -- Adopted vs Direct --
    adopted = [t for t in trades if t.get("adopted")]
    direct = [t for t in trades if not t.get("adopted")]

    tbl = Table(title="Adopted vs Direct")
    tbl.add_column("Source")
    tbl.add_column("Count", justify="right")
    tbl.add_column("Total P&L", justify="right")
    tbl.add_column("Avg P&L", justify="right")
    tbl.add_column("Win Rate", justify="right")
    tbl.add_column("Avg Hold", justify="right")
    for label, subset in [("Adopted", adopted), ("Direct", direct)]:
        if not subset:
            continue
        pnl = sum(t.get("realized_pnl", 0) for t in subset)
        avg = pnl / len(subset)
        wr = sum(1 for t in subset if (t.get("realized_pnl", 0) or 0) > 0) / len(subset) * 100
        ah = sum(t.get("hold_seconds", 0) or 0 for t in subset) / len(subset)
        c = "green" if pnl >= 0 else "red"
        tbl.add_row(label, str(len(subset)), f"[{c}]{_fmt_pnl(pnl)}[/{c}]", _fmt_pnl(avg), _fmt_pct(wr), _fmt_hold(ah))
    console.print(tbl)
    console.print()

    # -- Worst 10 --
    worst = sorted(trades, key=lambda t: t.get("realized_pnl", 0))[:10]
    tbl = Table(title="Top 10 Worst SYNC_CLOSE Trades")
    tbl.add_column("#", justify="right")
    tbl.add_column("Symbol")
    tbl.add_column("Mode")
    tbl.add_column("P&L", justify="right")
    tbl.add_column("Hold")
    tbl.add_column("Trigger")
    tbl.add_column("Exit Reason")
    for i, t in enumerate(worst, 1):
        tbl.add_row(
            str(i), t.get("symbol", "?"), t.get("entry_mode", "?"),
            f"[red]{_fmt_pnl(t.get('realized_pnl', 0))}[/red]",
            _fmt_hold(t.get("hold_seconds")),
            t.get("_trigger", "?"),
            t.get("exit_reason", ""),
        )
    console.print(tbl)
    console.print()

    # -- Recent N --
    recent = trades[-recent_n:]
    tbl = Table(title=f"Recent {len(recent)} SYNC_CLOSE Trades")
    tbl.add_column("Symbol")
    tbl.add_column("Mode")
    tbl.add_column("P&L", justify="right")
    tbl.add_column("Hold")
    tbl.add_column("Trigger")
    tbl.add_column("Adopted")
    tbl.add_column("Exit Time")
    for t in recent:
        pnl_val = t.get("realized_pnl", 0)
        c = "green" if pnl_val >= 0 else "red"
        tbl.add_row(
            t.get("symbol", "?"),
            t.get("entry_mode", "?"),
            f"[{c}]{_fmt_pnl(pnl_val)}[/{c}]",
            _fmt_hold(t.get("hold_seconds")),
            t.get("_trigger", "?"),
            "YES" if t.get("adopted") else "no",
            str(t.get("exit_time_utc", "?")),
        )
    console.print(tbl)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    default_jsonl = Path(__file__).resolve().parent.parent / "trade_behavior_log.jsonl"

    parser = argparse.ArgumentParser(description="SYNC_CLOSE Attribution Report")
    parser.add_argument("--jsonl", default=str(default_jsonl),
                        help="Path to trade_behavior_log.jsonl (default: sibling of scripts/)")
    parser.add_argument("--recent", type=int, default=20,
                        help="Number of recent trades to show (default: 20)")
    args = parser.parse_args()

    jsonl_path = args.jsonl
    if not os.path.isfile(jsonl_path):
        print(f"Error: JSONL file not found: {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    trades, skipped = load_sync_trades(jsonl_path)

    if HAS_RICH:
        print_rich(trades, skipped, args.recent)
    else:
        print_plain(trades, skipped, args.recent)


if __name__ == "__main__":
    main()
