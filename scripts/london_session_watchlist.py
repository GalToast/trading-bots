#!/usr/bin/env python3
"""
LONDON SESSION WATCHLIST
========================
When London opens at 07:00 UTC, what should we expect to fire and how will it perform?

Reads trade_behavior_log.jsonl, splits trades into London (07:00-16:00 UTC) vs
off-session, computes detailed statistics by symbol/signal/mode/combination, and
outputs a ranked watchlist of the top 10 symbol+signal combos to EXPECT during
London hours based on historical performance.

Runnable standalone:
    python scripts/london_session_watchlist.py
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOTS_DIR = Path(__file__).resolve().parent.parent  # trading-bots/
LOG_FILE = BOTS_DIR / "trade_behavior_log.jsonl"

# London session: 07:00 - 16:00 UTC (London open + NY overlap)
LONDON_START_HOUR = 7
LONDON_END_HOUR = 16  # exclusive upper bound


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_trades(log_path: Path) -> tuple[list[dict], int, int]:
    """Load trades from JSONL, returning (trades, valid_count, malformed_count)."""
    trades = []
    valid = 0
    malformed = 0

    if not log_path.exists():
        print(f"[ERROR] Log file not found: {log_path}")
        sys.exit(1)

    with open(log_path, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue

            entry_time = parse_entry_time(record)
            if entry_time is None:
                malformed += 1
                continue

            record["_entry_hour_utc"] = entry_time.hour
            trades.append(record)
            valid += 1

    return trades, valid, malformed


def parse_entry_time(record: dict) -> datetime | None:
    """Parse entry_time_utc from a trade record."""
    raw = record.get("entry_time_utc")
    if not raw:
        return None
    try:
        # Handle ISO format with timezone
        dt = datetime.fromisoformat(str(raw))
        # Convert to UTC
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------
def is_london(hour: int) -> bool:
    return LONDON_START_HOUR <= hour < LONDON_END_HOUR


def bucket_trades(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    london = []
    off = []
    for t in trades:
        if is_london(t["_entry_hour_utc"]):
            london.append(t)
        else:
            off.append(t)
    return london, off


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------
def pct_fmt(value: float, precision: int = 1) -> str:
    return f"{value:.{precision}f}%"


def pnl_fmt(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:.2f}"


def fmt_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def compute_group_stats(records: list[dict]) -> dict:
    """Compute trade count, green%, avg P&L, win rate, avg time-to-green for a group."""
    count = len(records)
    if count == 0:
        return {
            "count": 0,
            "green_pct": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
            "avg_time_to_green": None,
            "total_pnl": 0.0,
        }

    greens = 0
    wins = 0
    total_pnl = 0.0
    times_green = []

    for r in records:
        pnl = r.get("realized_pnl", 0.0) or 0.0
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        if r.get("first_green_before_fail", False):
            greens += 1

        ttg = r.get("time_to_first_green_seconds")
        if ttg is not None:
            try:
                ttg = float(ttg)
                times_green.append(ttg)
            except (ValueError, TypeError):
                pass

    return {
        "count": count,
        "green_pct": (greens / count) * 100,
        "avg_pnl": total_pnl / count,
        "win_rate": (wins / count) * 100,
        "avg_time_to_green": (sum(times_green) / len(times_green)) if times_green else None,
        "total_pnl": total_pnl,
    }


def group_by(trades: list[dict], key_fn) -> dict:
    groups: dict = defaultdict(list)
    for t in trades:
        key = key_fn(t)
        groups[key].append(t)
    return dict(groups)


def key_symbol(t: dict) -> str:
    return t.get("symbol", "UNKNOWN")


def key_signal(t: dict) -> str:
    return t.get("entry_signal_type", "UNKNOWN")


def key_mode(t: dict) -> str:
    return t.get("entry_mode", "UNKNOWN")


def key_symbol_signal(t: dict) -> str:
    return f"{t.get('symbol', '?')} + {t.get('entry_signal_type', '?')}"


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def render_stats_table(stats: dict, title: str = "", sort_key="total_pnl", top_n: int | None = None) -> Table | str:
    """Render a stats dict as a Rich table."""
    if not RICH_AVAILABLE:
        lines = [f"\n--- {title} ---"]
        for key, s in sorted(stats.items(), key=lambda x: x[1][sort_key], reverse=True):
            if top_n and len(lines) > top_n + 1:
                break
            lines.append(
                f"{key}: trades={s['count']}, green={pct_fmt(s['green_pct'])}, "
                f"avg_pnl={pnl_fmt(s['avg_pnl'])}, win={pct_fmt(s['win_rate'])}, "
                f"ttg={fmt_seconds(s['avg_time_to_green'])}, total={pnl_fmt(s['total_pnl'])}"
            )
        return "\n".join(lines)

    table = Table(title=title, box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Key", style="magenta", no_wrap=True)
    table.add_column("Trades", justify="right")
    table.add_column("Green%", justify="right")
    table.add_column("Avg P&L", justify="right")
    table.add_column("Win%", justify="right")
    table.add_column("Avg TTG", justify="right")
    table.add_column("Total P&L", justify="right")

    sorted_items = sorted(stats.items(), key=lambda x: x[1][sort_key], reverse=True)
    if top_n:
        sorted_items = sorted_items[:top_n]

    for key, s in sorted_items:
        table.add_row(
            key,
            str(s["count"]),
            pct_fmt(s["green_pct"]),
            pnl_fmt(s["avg_pnl"]),
            pct_fmt(s["win_rate"]),
            fmt_seconds(s["avg_time_to_green"]),
            pnl_fmt(s["total_pnl"]),
        )

    return table


def render_side_by_side(london_stats: dict, off_stats: dict, dimension: str, top_n: int = 20) -> Table | str:
    """Compare London vs off-session for a given dimension (symbol/signal/mode)."""
    all_keys = set(london_stats.keys()) | set(off_stats.keys())

    rows = []
    for key in all_keys:
        ls = london_stats.get(key, {"count": 0, "green_pct": 0, "avg_pnl": 0, "win_rate": 0, "total_pnl": 0})
        os_ = off_stats.get(key, {"count": 0, "green_pct": 0, "avg_pnl": 0, "win_rate": 0, "total_pnl": 0})
        pnl_diff = ls["total_pnl"] - os_["total_pnl"]
        win_diff = ls["win_rate"] - os_["win_rate"]
        rows.append((key, ls, os_, pnl_diff, win_diff))

    # Sort by London total P&L descending
    rows.sort(key=lambda r: r[1]["total_pnl"], reverse=True)
    if top_n:
        rows = rows[:top_n]

    if not RICH_AVAILABLE:
        lines = [f"\n--- {dimension}: London vs Off-Session ---"]
        for key, ls, os_, pnl_diff, win_diff in rows:
            better = "LONDON+" if pnl_diff > 0 else ("OFF+" if pnl_diff < 0 else "EQUAL")
            lines.append(
                f"{key} [{better}]: "
                f"LDN trades={ls['count']} avg={pnl_fmt(ls['avg_pnl'])} win={pct_fmt(ls['win_rate'])} total={pnl_fmt(ls['total_pnl'])} | "
                f"OFF trades={os_['count']} avg={pnl_fmt(os_['avg_pnl'])} win={pct_fmt(os_['win_rate'])} total={pnl_fmt(os_['total_pnl'])} | "
                f"dPnL={pnl_fmt(pnl_diff)} dWin={pct_fmt(win_diff)}"
            )
        return "\n".join(lines)

    table = Table(title=f"{dimension}: London vs Off-Session", box=box.ROUNDED, show_header=True, header_style="bold yellow")
    table.add_column(dimension, style="magenta", no_wrap=True)
    table.add_column("LDN#", justify="right")
    table.add_column("LDN Avg$", justify="right")
    table.add_column("LDN Win%", justify="right")
    table.add_column("LDN Total$", justify="right")
    table.add_column("OFF#", justify="right")
    table.add_column("OFF Avg$", justify="right")
    table.add_column("OFF Win%", justify="right")
    table.add_column("OFF Total$", justify="right")
    table.add_column("dPnL", justify="right")
    table.add_column("dWin%", justify="right")

    for key, ls, os_, pnl_diff, win_diff in rows:
        pnl_style = "green" if pnl_diff > 0 else ("red" if pnl_diff < 0 else "white")
        row = [
            key,
            str(ls["count"]),
            pnl_fmt(ls["avg_pnl"]),
            pct_fmt(ls["win_rate"]),
            pnl_fmt(ls["total_pnl"]),
            str(os_["count"]),
            pnl_fmt(os_["avg_pnl"]),
            pct_fmt(os_["win_rate"]),
            pnl_fmt(os_["total_pnl"]),
            Text(pnl_fmt(pnl_diff), style=pnl_style),
            pct_fmt(win_diff),
        ]
        table.add_row(*row)

    return table


def render_watchlist(combo_stats: dict, top_n: int = 10) -> Table | str:
    """Render the top London watchlist."""
    sorted_items = sorted(combo_stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True)[:top_n]

    if not RICH_AVAILABLE:
        lines = [f"\n{'='*70}", f"  LONDON SESSION WATCHLIST — Top {top_n} Symbol+Signal Combos", f"{'='*70}"]
        for rank, (key, s) in enumerate(sorted_items, 1):
            lines.append(
                f"  #{rank:<3} {key:<40}  trades={s['count']:>3}  avg={pnl_fmt(s['avg_pnl']):>10}  "
                f"win={pct_fmt(s['win_rate']):>6}  green={pct_fmt(s['green_pct']):>6}  "
                f"total={pnl_fmt(s['total_pnl']):>10}  ttg={fmt_seconds(s['avg_time_to_green'])}"
            )
        lines.append(f"{'='*70}")
        return "\n".join(lines)

    table = Table(
        title="[bold green on dark_green]  LONDON SESSION WATCHLIST — Top 10 Combos  ",
        box=box.DOUBLE_EDGE,
        show_header=True,
        header_style="bold white",
    )
    table.add_column("#", justify="center", style="bold cyan")
    table.add_column("Symbol + Signal", style="bold magenta", no_wrap=True)
    table.add_column("Trades", justify="right", style="white")
    table.add_column("Avg P&L", justify="right", style="white")
    table.add_column("Win%", justify="right", style="white")
    table.add_column("Green%", justify="right", style="white")
    table.add_column("Total P&L", justify="right", style="bold green")
    table.add_column("Avg TTG", justify="right", style="white")

    for rank, (key, s) in enumerate(sorted_items, 1):
        table.add_row(
            str(rank),
            key,
            str(s["count"]),
            pnl_fmt(s["avg_pnl"]),
            pct_fmt(s["win_rate"]),
            pct_fmt(s["green_pct"]),
            pnl_fmt(s["total_pnl"]),
            fmt_seconds(s["avg_time_to_green"]),
        )

    return table


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    console = Console() if RICH_AVAILABLE else None

    # --- Load ---
    trades, valid, malformed = load_trades(LOG_FILE)

    if RICH_AVAILABLE:
        console.print(Panel(
            f"[bold]Loaded[/bold] {valid} valid trades from {LOG_FILE.name}"
            + (f" | [yellow]{malformed} malformed lines skipped[/yellow]" if malformed else ""),
            title=":clock:  LONDON SESSION WATCHLIST",
            border_style="cyan",
        ))
    else:
        print(f"\nLoaded {valid} valid trades ({malformed} malformed skipped)\n")

    if not trades:
        print("No trades to analyze.")
        return

    # --- Bucket ---
    london_trades, off_trades = bucket_trades(trades)

    if RICH_AVAILABLE:
        london_panel = Text.assemble(
            (" London Session (07:00-16:00 UTC) ", "bold green"),
            (" — {} trades".format(len(london_trades)), "white"),
        )
        off_panel = Text.assemble(
            (" Off-Session ", "bold red"),
            (" — {} trades".format(len(off_trades)), "white"),
        )
        console.print(Panel(
            Text.assemble(london_panel, "\n", off_panel),
            border_style="yellow",
            title=":scales:  Trade Split",
        ))
    else:
        print(f"London (07:00-16:00 UTC): {len(london_trades)} trades")
        print(f"Off-Session: {len(off_trades)} trades\n")

    # --- Dimension groupings ---
    dimensions = {
        "By Symbol": key_symbol,
        "By Signal": key_signal,
        "By Mode": key_mode,
    }

    # --- Per-dimension tables ---
    for dim_name, key_fn in dimensions.items():
        london_groups = group_by(london_trades, key_fn)
        off_groups = group_by(off_trades, key_fn)

        london_stats = {k: compute_group_stats(v) for k, v in london_groups.items()}
        off_stats = {k: compute_group_stats(v) for k, v in off_groups.items()}

        if RICH_AVAILABLE:
            console.print()
            console.print(render_side_by_side(london_stats, off_stats, dim_name, top_n=20))
        else:
            print(render_side_by_side(london_stats, off_stats, dim_name, top_n=20))

    # --- Best combos during London ---
    london_combo_groups = group_by(london_trades, key_symbol_signal)
    london_combo_stats = {k: compute_group_stats(v) for k, v in london_combo_groups.items()}

    if RICH_AVAILABLE:
        console.print()
        console.print(
            render_stats_table(
                london_combo_stats,
                title=":fire:  Top 30 Symbol+Signal Combos During London Session",
                sort_key="total_pnl",
                top_n=30,
            )
        )

    # --- Watchlist (top 10) ---
    watchlist = render_watchlist(london_combo_stats, top_n=10)

    if RICH_AVAILABLE:
        console.print()
        console.print(watchlist)
    else:
        print(watchlist)

    # --- Quick summary: London edge ---
    london_total = compute_group_stats(london_trades)
    off_total = compute_group_stats(off_trades)

    if RICH_AVAILABLE:
        console.print()
        summary = Table(box=box.SIMPLE, show_header=False)
        summary.add_column("Metric")
        summary.add_column("London", justify="right", style="green")
        summary.add_column("Off-Session", justify="right", style="red")
        summary.add_column("Edge", justify="right", style="yellow")

        summary.add_row("Total P&L", pnl_fmt(london_total["total_pnl"]), pnl_fmt(off_total["total_pnl"]), "")
        summary.add_row("Avg P&L/trade", pnl_fmt(london_total["avg_pnl"]), pnl_fmt(off_total["avg_pnl"]), "")
        summary.add_row("Win Rate", pct_fmt(london_total["win_rate"]), pct_fmt(off_total["win_rate"]), "")
        summary.add_row("Green%", pct_fmt(london_total["green_pct"]), pct_fmt(off_total["green_pct"]), "")
        summary.add_row("Avg TTG", fmt_seconds(london_total["avg_time_to_green"]), fmt_seconds(off_total["avg_time_to_green"]), "")

        console.print(Panel(summary, title=":chart_with_upwards_trend:  London Edge Summary", border_style="green"))
    else:
        print(f"\n{'='*50}")
        print("LONDON EDGE SUMMARY")
        print(f"{'='*50}")
        print(f"  {'':20s}  London          Off-Session")
        print(f"  {'Total P&L':20s}  {pnl_fmt(london_total['total_pnl']):>14s}  {pnl_fmt(off_total['total_pnl']):>14s}")
        print(f"  {'Avg P&L/trade':20s}  {pnl_fmt(london_total['avg_pnl']):>14s}  {pnl_fmt(off_total['avg_pnl']):>14s}")
        print(f"  {'Win Rate':20s}  {pct_fmt(london_total['win_rate']):>14s}  {pct_fmt(off_total['win_rate']):>14s}")
        print(f"  {'Green%':20s}  {pct_fmt(london_total['green_pct']):>14s}  {pct_fmt(off_total['green_pct']):>14s}")
        print(f"  {'Avg TTG':20s}  {fmt_seconds(london_total['avg_time_to_green']):>14s}  {fmt_seconds(off_total['avg_time_to_green']):>14s}")


if __name__ == "__main__":
    main()
