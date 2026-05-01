#!/usr/bin/env python3
"""Performance dashboard for trading bot trade_behavior_log.jsonl."""

import json
import sys
import os
import re
from datetime import datetime, timezone, date
from collections import defaultdict
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    console = Console()
    USE_RICH = True
except ImportError:
    USE_RICH = False

    class _FallbackConsole:
        @staticmethod
        def print(msg=""):
            print(msg)

        @staticmethod
        def rule(title="", *_, **__):
            sep = "-" * 60
            print(f"\n{sep}\n{title}\n{sep}")

    console = _FallbackConsole()


LOG_FILE = Path(__file__).resolve().parent.parent / "trade_behavior_log.jsonl"


def parse_exit_reason_category(raw: str) -> str:
    """Extract the short exit category, e.g. 'TRAIL' from 'TRAIL (peak $+7.70...)'."""
    if not raw:
        return "UNKNOWN"
    cat = raw.split("(")[0].split("[")[0].strip()
    if not cat:
        return "UNKNOWN"
    # Collapse SYNC_CLOSE variants into a single category
    if cat.startswith("SYNC_CLOSE"):
        return "SYNC_CLOSE"
    # Collapse ZOMBIE variants
    if cat.startswith("ZOMBIE"):
        return "ZOMBIE"
    return cat


def load_trades(path: Path) -> list[dict]:
    """Load trades from JSONL, skipping malformed lines."""
    trades = []
    skipped = 0
    if not path.exists():
        print(f"Log file not found: {path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                # Normalize timestamp field for sorting/filtering
                ts = trade.get("exit_time_utc") or trade.get("recorded_at_utc") or ""
                trade["_ts"] = ts
                # Parse date for "today" filtering
                trade["_date"] = None
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts)
                        trade["_date"] = dt.date()
                    except (ValueError, TypeError):
                        pass
                # Extract exit reason category
                trade["_exit_cat"] = parse_exit_reason_category(trade.get("exit_reason", ""))
                # Ensure pnl is numeric
                try:
                    trade["_pnl"] = float(trade.get("realized_pnl", 0) or 0)
                except (ValueError, TypeError):
                    trade["_pnl"] = 0.0
                trades.append(trade)
            except json.JSONDecodeError:
                skipped += 1

    # Sort by exit time ascending
    trades.sort(key=lambda t: t.get("_ts", ""))

    if skipped:
        console.print(f"[yellow]Skipped {skipped} malformed line(s)[/]")

    return trades


def fmt_pnl(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    if USE_RICH:
        color = "green" if pnl >= 0 else "red"
        return f"[{color}]{sign}${pnl:.2f}[/]"
    return f"{sign}${pnl:.2f}"


def pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{n / total * 100:.1f}%"


def section_title(title: str):
    if USE_RICH:
        console.rule(f"[bold cyan]{title}[/]")
    else:
        console.rule(title)


def print_overview(trades: list[dict]):
    total = len(trades)
    wins = [t for t in trades if t["_pnl"] > 0]
    losses = [t for t in trades if t["_pnl"] < 0]
    breakeven = total - len(wins) - len(losses)
    total_pnl = sum(t["_pnl"] for t in trades)
    avg_win = sum(t["_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["_pnl"] for t in losses) / len(losses) if losses else 0
    expectancy = total_pnl / total if total else 0

    if USE_RICH:
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="dim")
        table.add_column()

        rows = [
            ("Total Trades", str(total)),
            ("Wins", f"[green]{len(wins)}[/] ({pct(len(wins), total)})"),
            ("Losses", f"[red]{len(losses)}[/] ({pct(len(losses), total)})"),
            ("Breakeven", str(breakeven)),
            ("Win Rate", pct(len(wins), total)),
            ("", ""),
            ("Total P&L", fmt_pnl(total_pnl)),
            ("Average Win", f"[green]${avg_win:.2f}[/]"),
            ("Average Loss", f"[red]${avg_loss:.2f}[/]"),
            ("Expectancy/Trade", fmt_pnl(expectancy)),
        ]
        for label, value in rows:
            table.add_row(label, value)

        console.print(Panel(table, title="[bold]Overview[/]", border_style="cyan"))
    else:
        print(f"Total Trades: {total}")
        print(f"Wins: {len(wins)} ({pct(len(wins), total)})")
        print(f"Losses: {len(losses)} ({pct(len(losses), total)})")
        print(f"Breakeven: {breakeven}")
        print(f"Win Rate: {pct(len(wins), total)}")
        print(f"Total P&L: {fmt_pnl(total_pnl)}")
        print(f"Average Win: ${avg_win:.2f}")
        print(f"Average Loss: ${avg_loss:.2f}")
        print(f"Expectancy/Trade: {fmt_pnl(expectancy)}")
        print()


def _print_by_group(title: str, groups: dict, sort_key="pnl"):
    """Print a ranked table of groups (symbols, modes, exit reasons)."""
    stats = []
    for key, items in groups.items():
        pnl = sum(t["_pnl"] for t in items)
        wins = sum(1 for t in items if t["_pnl"] > 0)
        count = len(items)
        wr = wins / count * 100 if count else 0
        stats.append((key, count, wins, pnl, wr))

    if sort_key == "pnl":
        stats.sort(key=lambda x: x[3], reverse=True)
    else:
        stats.sort(key=lambda x: x[4], reverse=True)

    if USE_RICH:
        table = Table(box=box.SIMPLE, show_lines=False)
        table.add_column("#", style="dim", width=3)
        table.add_column(title.capitalize(), style="bold")
        table.add_column("Trades", justify="right")
        table.add_column("Wins", justify="right")
        table.add_column("P&L", justify="right", min_width=12)
        table.add_column("Win %", justify="right")

        for i, (name, count, wins, pnl, wr) in enumerate(stats, 1):
            table.add_row(
                str(i),
                name,
                str(count),
                f"[green]{wins}[/]" if count > 0 else "0",
                fmt_pnl(pnl),
                f"{wr:.1f}%",
            )
        console.print(table)
    else:
        print(f"\n{title.upper()} (by P&L):")
        print(f"{'#':<4} {title.capitalize():<16} {'Trades':>6} {'Wins':>6} {'P&L':>12} {'Win%':>7}")
        for i, (name, count, wins, pnl, wr) in enumerate(stats, 1):
            sign = "+" if pnl >= 0 else ""
            print(f"{i:<4} {name:<16} {count:>6} {wins:>6} {sign}${pnl:>10.2f} {wr:>6.1f}%")


def print_symbols(trades: list[dict]):
    section_title("Symbols")
    groups = defaultdict(list)
    for t in trades:
        groups[t.get("symbol", "UNKNOWN")].append(t)
    print("  [dim]By P&L[/]" if USE_RICH else "  By P&L:")
    _print_by_group("symbol", groups, "pnl")
    print()
    if USE_RICH:
        console.print("  [dim]By Win Rate[/]")
    else:
        print("  By Win Rate:")
    _print_by_group("symbol", groups, "winrate")


def print_modes(trades: list[dict]):
    section_title("Modes")
    groups = defaultdict(list)
    for t in trades:
        groups[t.get("entry_mode", "UNKNOWN")].append(t)
    print("  [dim]By P&L[/]" if USE_RICH else "  By P&L:")
    _print_by_group("mode", groups, "pnl")
    print()
    if USE_RICH:
        console.print("  [dim]By Win Rate[/]")
    else:
        print("  By Win Rate:")
    _print_by_group("mode", groups, "winrate")


def print_exit_reasons(trades: list[dict]):
    section_title("Exit Reasons")
    groups = defaultdict(list)
    for t in trades:
        groups[t["_exit_cat"]].append(t)
    print("  [dim]By P&L[/]" if USE_RICH else "  By P&L:")
    _print_by_group("exit_reason", groups, "pnl")
    print()
    if USE_RICH:
        console.print("  [dim]By Win Rate[/]")
    else:
        print("  By Win Rate:")
    _print_by_group("exit_reason", groups, "winrate")


def print_recent_trades(trades: list[dict], n: int = 20):
    section_title(f"Recent {n} Trades")
    recent = trades[-n:]

    if USE_RICH:
        table = Table(box=box.SIMPLE, padding=(0, 1))
        table.add_column("Time", style="dim")
        table.add_column("Symbol", style="bold", max_width=9)
        table.add_column("Mode", max_width=11)
        table.add_column("Dir", max_width=4)
        table.add_column("P&L", justify="right")
        table.add_column("Hold", justify="right")
        table.add_column("Exit Reason", max_width=36)

        for t in reversed(recent):
            ts = t.get("exit_time_utc", "")[:19] if t.get("exit_time_utc") else ""
            hold = t.get("hold_seconds")
            hold_str = f"{int(hold)}s" if hold is not None else "-"
            # Show just the category, optionally with a short parenthetical
            raw_reason = t.get("exit_reason", "") or ""
            if len(raw_reason) > 36:
                # Truncate the parenthetical portion
                paren_idx = raw_reason.find("(")
                if paren_idx >= 0:
                    cat = raw_reason[:paren_idx].strip()
                    detail = raw_reason[paren_idx:paren_idx + 28] + ")" if len(raw_reason) > paren_idx + 28 else raw_reason[paren_idx:]
                    reason_display = f"{cat} {detail}"
                else:
                    reason_display = raw_reason[:36]
            else:
                reason_display = raw_reason
            table.add_row(
                ts,
                t.get("symbol", "?"),
                t.get("entry_mode", "?"),
                t.get("direction", "?")[:4],
                fmt_pnl(t["_pnl"]),
                hold_str,
                reason_display[:36],
            )
        console.print(table)
    else:
        print(f"{'Time':<21} {'Symbol':<10} {'Mode':<14} {'Dir':<4} {'P&L':>10}  Exit Reason")
        for t in reversed(recent):
            ts = t.get("exit_time_utc", "")[:19] if t.get("exit_time_utc") else ""
            sign = "+" if t["_pnl"] >= 0 else ""
            print(f"{ts:<21} {t.get('symbol', '?'):<10} {t.get('entry_mode', '?'):<14} {t.get('direction', '?')[:4]:<4} {sign}${t['_pnl']:>9.2f}  {t.get('exit_reason', '')[:50]}")


def print_today_stats(trades: list[dict]):
    today_utc = date.today()
    today_trades = [t for t in trades if t["_date"] == today_utc]

    section_title(f"Today ({today_utc})")

    if not today_trades:
        if USE_RICH:
            console.print("[yellow]No trades recorded today (UTC).[/]")
        else:
            print("No trades recorded today (UTC).")
        return

    total = len(today_trades)
    wins = [t for t in today_trades if t["_pnl"] > 0]
    losses = [t for t in today_trades if t["_pnl"] < 0]
    total_pnl = sum(t["_pnl"] for t in today_trades)
    avg_win = sum(t["_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["_pnl"] for t in losses) / len(losses) if losses else 0

    if USE_RICH:
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="dim")
        table.add_column()
        rows = [
            ("Trades", str(total)),
            ("Wins", f"[green]{len(wins)}[/] ({pct(len(wins), total)})"),
            ("Losses", f"[red]{len(losses)}[/] ({pct(len(losses), total)})"),
            ("Win Rate", pct(len(wins), total)),
            ("", ""),
            ("Total P&L", fmt_pnl(total_pnl)),
            ("Average Win", f"[green]${avg_win:.2f}[/]"),
            ("Average Loss", f"[red]${avg_loss:.2f}[/]"),
        ]
        for label, value in rows:
            table.add_row(label, value)
        console.print(Panel(table, title=f"[bold]Today -- {today_utc} (UTC)[/]", border_style="yellow"))
    else:
        print(f"Trades: {total}")
        print(f"Wins: {len(wins)} ({pct(len(wins), total)})")
        print(f"Losses: {len(losses)} ({pct(len(losses), total)})")
        print(f"Win Rate: {pct(len(wins), total)}")
        sign = "+" if total_pnl >= 0 else ""
        print(f"Total P&L: {sign}${total_pnl:.2f}")
        print(f"Average Win: ${avg_win:.2f}")
        print(f"Average Loss: ${avg_loss:.2f}")


def main():
    if USE_RICH:
        console.print(Panel.fit(
            "[bold cyan]Trading Bot Performance Dashboard[/]",
            subtitle=f"[dim]{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}[/]",
            border_style="cyan",
        ))
    else:
        print("=" * 60)
        print("  Trading Bot Performance Dashboard")
        print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("=" * 60)

    trades = load_trades(LOG_FILE)

    if not trades:
        print("No trades found in log.")
        return

    print_overview(trades)
    print_symbols(trades)
    print()
    print_modes(trades)
    print()
    print_exit_reasons(trades)
    print()
    print_recent_trades(trades, 20)
    print()
    print_today_stats(trades)

    if USE_RICH:
        console.print(f"\n[dim]Source: {LOG_FILE} | {len(trades)} trades loaded[/]")
    else:
        print(f"\nSource: {LOG_FILE} | {len(trades)} trades loaded")


if __name__ == "__main__":
    main()
