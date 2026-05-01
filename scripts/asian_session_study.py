"""
Asian Session Trading Study
============================
Analyzes trade_behavior_log.jsonl to answer:
"Can we profitably trade during Asian session, and if so, how?"

Usage:
    python scripts/asian_session_study.py

Reads trade_behavior_log.jsonl from the trading-bots root directory.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console(force_terminal=True, width=140)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
LOG_FILE = REPO_DIR / "trade_behavior_log.jsonl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ASIAN_START = 0   # 00:00 UTC inclusive
ASIAN_END = 7     # 07:59 UTC inclusive  (00:00-07:59 = hours 0..7)


def parse_utc_hour(ts: str) -> int | None:
    """Extract UTC hour from an ISO timestamp string."""
    if not ts or "T" not in ts:
        return None
    try:
        time_part = ts.split("T")[1]
        return int(time_part[:2])
    except (ValueError, IndexError):
        return None


def is_asian_session(hour: int) -> bool:
    return ASIAN_START <= hour <= ASIAN_END


def green(text: str) -> Text:
    return Text(str(text), style="bold green")


def red(text: str) -> Text:
    return Text(str(text), style="bold red")


def yellow(text: str) -> Text:
    return Text(str(text), style="bold yellow")


def cyan(text: str) -> Text:
    return Text(str(text), style="bold cyan")


def money(val: float) -> Text:
    if val >= 0:
        return green(f"${val:+.2f}")
    return red(f"${val:+.2f}")


def pct(val: float) -> Text:
    if val >= 50:
        return green(f"{val:.1f}%")
    return red(f"{val:.1f}%")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_trades(path: Path) -> list[dict]:
    trades = []
    malformed = 0
    if not path.exists():
        console.print(f"[bold red]Log file not found: {path}[/bold red]")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
                hour = parse_utc_hour(record.get("entry_time_utc", ""))
                if hour is None:
                    malformed += 1
                    continue
                record["_hour"] = hour
                trades.append(record)
            except (json.JSONDecodeError, KeyError):
                malformed += 1

    console.print(f"[dim]Loaded {len(trades)} trades ({malformed} malformed lines skipped)[/dim]")
    return trades


# ---------------------------------------------------------------------------
# 1. Hour-of-day breakdown (0-23 UTC)
# ---------------------------------------------------------------------------
def hour_of_day_breakdown(trades: list[dict]) -> None:
    console.rule("[bold]HOUR-OF-DAY BREAKDOWN (UTC)")

    hourly = defaultdict(lambda: {"count": 0, "total_pnl": 0.0, "wins": 0, "greens": 0})

    for t in trades:
        h = t["_hour"]
        pnl = t.get("realized_pnl", 0) or 0.0
        hourly[h]["count"] += 1
        hourly[h]["total_pnl"] += pnl
        if pnl > 0:
            hourly[h]["wins"] += 1
            hourly[h]["greens"] += 1
        elif pnl == 0:
            hourly[h]["greens"] += 0.5  # breakeven = half

    table = Table(
        title="Profitability by Hour of Day (UTC)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on blue",
    )
    table.add_column("Hour", justify="center", width=6)
    table.add_column("Trades", justify="right", width=8)
    table.add_column("Total P&L", justify="right", width=12)
    table.add_column("Avg P&L", justify="right", width=10)
    table.add_column("Win Rate", justify="right", width=10)
    table.add_column("Green%", justify="right", width=9)
    table.add_column("Bar", justify="left", width=30)

    max_abs_pnl = max((abs(v["total_pnl"]) for v in hourly.values()), default=1) or 1

    for h in range(24):
        d = hourly[h]
        count = d["count"]
        if count == 0:
            table.add_row(
                Text(f"{h:02d}:00", style="dim"),
                Text("0", style="dim"),
                Text("$0.00", style="dim"),
                Text("$0.00", style="dim"),
                Text("--", style="dim"),
                Text("--", style="dim"),
                Text("--", style="dim"),
            )
            continue

        total_pnl = d["total_pnl"]
        avg_pnl = total_pnl / count
        win_rate = (d["wins"] / count) * 100
        green_pct = (d["greens"] / count) * 100

        bar_len = int((abs(total_pnl) / max_abs_pnl) * 25)
        if total_pnl >= 0:
            bar = Text("\u2588" * bar_len, style="green")
        else:
            bar = Text("\u2588" * bar_len, style="red")

        table.add_row(
            Text(f"{h:02d}:00", style="green" if total_pnl >= 0 else "red"),
            Text(str(count), style="green" if total_pnl >= 0 else "red"),
            money(total_pnl),
            money(avg_pnl),
            pct(win_rate),
            pct(green_pct),
            bar,
        )

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# 2. Asian Session Analysis (00:00-07:00 UTC)
# ---------------------------------------------------------------------------
def asian_session_analysis(trades: list[dict]) -> tuple[list[dict], dict, dict]:
    asian_trades = [t for t in trades if is_asian_session(t["_hour"])]

    console.rule("[bold]ASIAN SESSION ANALYSIS (00:00-07:59 UTC)")

    if not asian_trades:
        console.print("[yellow]No trades found during Asian session hours.[/yellow]")
        console.print()
        return [], {}, {}

    # Overall summary
    total_pnl = sum(t.get("realized_pnl", 0) or 0.0 for t in asian_trades)
    wins = sum(1 for t in asian_trades if (t.get("realized_pnl", 0) or 0.0) > 0)
    win_rate = (wins / len(asian_trades)) * 100

    summary = Table(
        title="Asian Session Overview",
        box=box.ROUNDED,
        show_header=False,
    )
    summary.add_column("Metric", style="bold cyan")
    summary.add_column("Value")
    summary.add_row("Total Trades", str(len(asian_trades)))
    summary.add_row("Total P&L", money(total_pnl))
    summary.add_row("Win Rate", pct(win_rate))
    summary.add_row("Avg P&L/Trade", money(total_pnl / len(asian_trades)))
    console.print(summary)
    console.print()

    # --- By Symbol: Activity ---
    by_symbol = defaultdict(lambda: {"count": 0, "total_pnl": 0.0, "wins": 0, "greens": 0})
    for t in asian_trades:
        sym = t.get("symbol", "UNKNOWN")
        pnl = t.get("realized_pnl", 0) or 0.0
        by_symbol[sym]["count"] += 1
        by_symbol[sym]["total_pnl"] += pnl
        if pnl > 0:
            by_symbol[sym]["wins"] += 1
            by_symbol[sym]["greens"] += 1
        elif pnl == 0:
            by_symbol[sym]["greens"] += 0.5

    # Most active symbols
    active_sorted = sorted(by_symbol.items(), key=lambda x: x[1]["count"], reverse=True)
    table_active = Table(
        title="Asian Session: Most Active Symbols",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on blue",
    )
    table_active.add_column("Rank", justify="center", width=6)
    table_active.add_column("Symbol", justify="left", width=12)
    table_active.add_column("Trades", justify="right", width=8)
    table_active.add_column("Total P&L", justify="right", width=12)
    table_active.add_column("Avg P&L", justify="right", width=10)
    table_active.add_column("Win Rate", justify="right", width=10)
    table_active.add_column("Green%", justify="right", width=9)

    for i, (sym, d) in enumerate(active_sorted, 1):
        c = d["count"]
        tp = d["total_pnl"]
        ap = tp / c
        wr = (d["wins"] / c) * 100
        gp = (d["greens"] / c) * 100
        table_active.add_row(
            Text(str(i), style="bold"),
            Text(sym, style="cyan"),
            str(c),
            money(tp),
            money(ap),
            pct(wr),
            pct(gp),
        )
    console.print(table_active)
    console.print()

    # Most profitable symbols
    profitable_sorted = sorted(by_symbol.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
    table_profit = Table(
        title="Asian Session: Most Profitable Symbols",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on blue",
    )
    table_profit.add_column("Rank", justify="center", width=6)
    table_profit.add_column("Symbol", justify="left", width=12)
    table_profit.add_column("Trades", justify="right", width=8)
    table_profit.add_column("Total P&L", justify="right", width=12)
    table_profit.add_column("Avg P&L", justify="right", width=10)
    table_profit.add_column("Win Rate", justify="right", width=10)
    table_profit.add_column("Green%", justify="right", width=9)

    for i, (sym, d) in enumerate(profitable_sorted, 1):
        c = d["count"]
        tp = d["total_pnl"]
        ap = tp / c
        wr = (d["wins"] / c) * 100
        gp = (d["greens"] / c) * 100
        table_profit.add_row(
            Text(str(i), style="bold"),
            Text(sym, style="green" if tp >= 0 else "red"),
            str(c),
            money(tp),
            money(ap),
            pct(wr),
            pct(gp),
        )
    console.print(table_profit)
    console.print()

    # --- By Signal Type ---
    by_signal = defaultdict(lambda: {"count": 0, "total_pnl": 0.0, "wins": 0, "greens": 0})
    for t in asian_trades:
        sig = t.get("entry_signal_type", "unknown") or "unknown"
        pnl = t.get("realized_pnl", 0) or 0.0
        by_signal[sig]["count"] += 1
        by_signal[sig]["total_pnl"] += pnl
        if pnl > 0:
            by_signal[sig]["wins"] += 1
            by_signal[sig]["greens"] += 1
        elif pnl == 0:
            by_signal[sig]["greens"] += 0.5

    signal_sorted = sorted(by_signal.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
    table_signal = Table(
        title="Asian Session: Signal Type Performance",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on blue",
    )
    table_signal.add_column("Signal Type", justify="left", width=30)
    table_signal.add_column("Trades", justify="right", width=8)
    table_signal.add_column("Total P&L", justify="right", width=12)
    table_signal.add_column("Avg P&L", justify="right", width=10)
    table_signal.add_column("Win Rate", justify="right", width=10)
    table_signal.add_column("Green%", justify="right", width=9)

    for sig, d in signal_sorted:
        c = d["count"]
        tp = d["total_pnl"]
        ap = tp / c
        wr = (d["wins"] / c) * 100
        gp = (d["greens"] / c) * 100
        table_signal.add_row(
            Text(sig, style="green" if tp >= 0 else "red"),
            str(c),
            money(tp),
            money(ap),
            pct(wr),
            pct(gp),
        )
    console.print(table_signal)
    console.print()

    return asian_trades, dict(by_symbol), dict(by_signal)


# ---------------------------------------------------------------------------
# 3. Best 3-Hour Window (sliding within 00:00-07:00 UTC)
# ---------------------------------------------------------------------------
def best_3hour_window(trades: list[dict]) -> dict:
    asian_trades = [t for t in trades if is_asian_session(t["_hour"])]

    console.rule("[bold]BEST 3-HOUR WINDOW ANALYSIS (sliding within 00:00-07:00 UTC)")

    # Windows: 0-2, 1-3, 2-4, 3-5, 4-6, 5-7 (each inclusive of both endpoints)
    # Actually: [start, start+1, start+2] for start in 0..5
    # Plus [5,6,7] as the last valid window
    results = []

    for start in range(0, 6):  # start hours 0 through 5
        end = start + 2  # inclusive end hour
        window_trades = [t for t in asian_trades if start <= t["_hour"] <= end]
        total_pnl = sum(t.get("realized_pnl", 0) or 0.0 for t in window_trades)
        wins = sum(1 for t in window_trades if (t.get("realized_pnl", 0) or 0.0) > 0)
        count = len(window_trades)
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl = (total_pnl / count) if count > 0 else 0

        results.append({
            "start": start,
            "end": end,
            "label": f"{start:02d}:00-{end:02d}:59",
            "count": count,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "win_rate": win_rate,
            "wins": wins,
        })

    # Sort by total P&L descending
    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    best = results[0]

    table = Table(
        title="3-Hour Window Rankings",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on blue",
    )
    table.add_column("Rank", justify="center", width=6)
    table.add_column("Window (UTC)", justify="center", width=18)
    table.add_column("Trades", justify="right", width=8)
    table.add_column("Total P&L", justify="right", width=12)
    table.add_column("Avg P&L", justify="right", width=10)
    table.add_column("Win Rate", justify="right", width=10)

    for i, r in enumerate(results, 1):
        is_best = i == 1
        style = "bold green" if is_best else ("green" if r["total_pnl"] >= 0 else "red")
        table.add_row(
            Text(str(i), style="bold yellow" if is_best else "white"),
            Text(r["label"], style=style),
            Text(str(r["count"]), style=style),
            money(r["total_pnl"]),
            money(r["avg_pnl"]),
            pct(r["win_rate"]),
        )

    console.print(table)
    console.print()
    return best


# ---------------------------------------------------------------------------
# 4. Recommendation
# ---------------------------------------------------------------------------
def recommendation(
    best_window: dict,
    by_symbol: dict,
    by_signal: dict,
    asian_trades: list[dict],
) -> None:
    console.rule("[bold]RECOMMENDATION")

    asian_pnl = sum(t.get("realized_pnl", 0) or 0.0 for t in asian_trades)
    can_profit = asian_pnl > 0

    # Best symbol during Asian (by total P&L, min 1 trade)
    best_sym = max(
        ((s, d) for s, d in by_symbol.items() if d["count"] > 0),
        key=lambda x: x[1]["total_pnl"],
        default=None,
    )

    # Best signal during Asian (by total P&L, min 1 trade)
    best_sig = max(
        ((s, d) for s, d in by_signal.items() if d["count"] > 0),
        key=lambda x: x[1]["total_pnl"],
        default=None,
    )

    lines = []

    if can_profit:
        lines.append(Text("VERDICT: ", style="bold green"))
        lines.append(Text("Asian session trading appears PROFITABLE based on current data.", style="bold green"))
    else:
        lines.append(Text("VERDICT: ", style="bold red"))
        lines.append(Text("Asian session is NET NEGATIVE. Only trade if you must.", style="bold yellow"))

    lines.append(Text(""))

    sym_name = best_sym[0] if best_sym else "N/A"
    sig_name = best_sig[0] if best_sig else "N/A"
    window_label = best_window["label"]

    rec_text = (
        f"If you MUST trade off-session, trade "
        f"[bold cyan]{sym_name}[/bold cyan] "
        f"during [bold cyan]{window_label}[/bold cyan] UTC hours "
        f"using [bold cyan]{sig_name}[/bold cyan] signals."
    )
    lines.append(Text.from_markup(rec_text))

    lines.append(Text(""))

    # Supporting stats
    if best_sym:
        s, d = best_sym
        lines.append(Text(f"  - Best symbol: {s} ({d['count']} trades, ${d['total_pnl']:+.2f} total, {d['wins']/d['count']*100:.0f}% win rate)"))
    if best_sig:
        s, d = best_sig
        lines.append(Text(f"  - Best signal: {s} ({d['count']} trades, ${d['total_pnl']:+.2f} total, {d['wins']/d['count']*100:.0f}% win rate)"))

    lines.append(Text(f"  - Best window: {window_label} UTC ({best_window['count']} trades, ${best_window['total_pnl']:+.2f})"))
    lines.append(Text(f"  - Asian session total: {len(asian_trades)} trades, ${asian_pnl:+.2f}"))

    # Build as a single Text with newlines between each piece
    combined = Text()
    for i, piece in enumerate(lines):
        if i > 0:
            combined.append("\n")
        combined.append(piece)

    rec_panel = Panel(
        combined,
        title="Off-Session Trading Recommendation",
        border_style="yellow" if can_profit else "red",
        expand=False,
    )
    console.print(rec_panel)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    console.print(Panel.fit(
        Text.from_markup("[bold]Asian Session Trading Study[/bold]\n[dim]Can we profitably trade during Asian session, and if so, how?[/dim]"),
        border_style="cyan",
    ))
    console.print()

    trades = load_trades(LOG_FILE)

    if not trades:
        console.print("[bold red]No valid trades found in log.[/bold red]")
        sys.exit(1)

    # 1. Hour-of-day breakdown
    hour_of_day_breakdown(trades)

    # 2. Asian session analysis
    asian_trades, by_symbol, by_signal = asian_session_analysis(trades)

    # 3. Best 3-hour window
    best_window = best_3hour_window(trades)

    # 4. Recommendation
    if asian_trades and by_symbol and by_signal:
        recommendation(best_window, by_symbol, by_signal, asian_trades)
    else:
        console.print("[yellow]Insufficient Asian session data for a recommendation.[/yellow]")


if __name__ == "__main__":
    main()
