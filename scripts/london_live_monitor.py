#!/usr/bin/env python3
"""
LONDON LIVE MONITOR
===================
Real-time dashboard for London session monitoring.
Reads last 30 trades from trade_behavior_log.jsonl and last 100 lines
from mt5_canonical_worker_out.log, computes a quick snapshot, and
checks against the London watchlist top combos.

Usage:
    python scripts/london_live_monitor.py          # one-shot
    python scripts/london_live_monitor.py --watch  # poll every 30s
"""

import json
import sys
import time
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    RICH = True
except ImportError:
    RICH = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOTS_DIR = Path(__file__).resolve().parent.parent
TRADE_LOG = BOTS_DIR / "trade_behavior_log.jsonl"
WORKER_LOG = BOTS_DIR / "mt5_canonical_worker_out.log"

# London watchlist — top combos from historical London performance
# Hardcoded from scripts/london_session_watchlist.py analysis
LONDON_WATCHLIST = [
    ("EURHKD", "unlabeled"),
    ("NAS100", "trend_continuation"),
    ("GER30", "unlabeled"),
    ("US30", "unlabeled"),
    ("AUDCHF", "gemini_buy"),
    ("USDCHF", "breakout_hold_above_high"),
    ("USDCHF", "gemini_buy"),
    ("USDCHF", "range_mean_reversion"),
    ("AUDCHF", "unlabeled"),
    ("USDCHF", "pullback_to_structure_hold"),
]

# Session windows (UTC)
ASIAN_HOURS = (0, 7)    # 00:00-06:59
LONDON_HOURS = (7, 16)  # 07:00-15:59
NY_HOURS = (13, 21)     # 13:00-20:59 (overlaps London 13-16)

POLL_INTERVAL = 30  # seconds for --watch mode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:.2f}"


def fmt_seconds(s: float | None) -> str:
    if s is None:
        return "-"
    m, sec = divmod(int(s), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"
    return f"{m}m{sec:02d}s"


def pct_fmt(v: float) -> str:
    return f"{v:.1f}%"


def color_pnl(v: float) -> tuple[str, str]:
    """Return (text, style) for rich rendering."""
    sign = "+" if v >= 0 else ""
    text = f"{sign}${v:.2f}"
    if v > 0:
        style = "bold green"
    elif v < 0:
        style = "bold red"
    else:
        style = "white"
    return text, style


def get_session_name(utc_hour: int) -> str:
    """Return current session label based on UTC hour."""
    in_london = LONDON_HOURS[0] <= utc_hour < LONDON_HOURS[1]
    in_ny = NY_HOURS[0] <= utc_hour < NY_HOURS[1]
    in_asian = ASIAN_HOURS[0] <= utc_hour < ASIAN_HOURS[1]

    if in_london and in_ny:
        return "London/NY Overlap"
    if in_ny:
        return "New York"
    if in_london:
        return "London"
    if in_asian:
        return "Asian"
    return "Late / Off-Session"


def is_good_window(utc_hour: int) -> tuple[bool, str]:
    """Determine if current time is a good trading window."""
    if LONDON_HOURS[0] <= utc_hour < LONDON_HOURS[1]:
        overlap = " (NY overlap)" if utc_hour >= NY_HOURS[0] else ""
        return True, f"London session{overlap} — prime window"
    if NY_HOURS[0] <= utc_hour < NY_HOURS[1]:
        return True, "New York session — decent liquidity"
    if ASIAN_HOURS[0] <= utc_hour < ASIAN_HOURS[1]:
        return False, "Asian session — low volatility, high threshold needed"
    return False, "Off-session — reduced edge"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_recent_trades(log_path: Path, n: int = 30) -> list[dict]:
    """Read the last n valid JSON lines from trade_behavior_log.jsonl."""
    if not log_path.exists():
        return []
    trades = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    trades.append(obj)
                except json.JSONDecodeError:
                    pass
    except Exception:
        return []
    return trades[-n:]


def load_recent_worker_lines(log_path: Path, n: int = 100) -> list[str]:
    """Read the last n lines from the worker log."""
    if not log_path.exists():
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n:]]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def analyze_trades(trades: list[dict]):
    """Compute win/loss, P&L, avg hold time from a list of trade dicts."""
    wins = 0
    losses = 0
    breakeven = 0
    total_pnl = 0.0
    hold_times = []
    symbols_seen: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0, "wins": 0})

    for t in trades:
        pnl = t.get("realized_pnl", 0.0) or 0.0
        hold = t.get("hold_seconds")
        sym = t.get("symbol", "?")
        sig = t.get("entry_signal_type", "?")

        total_pnl += pnl
        if hold is not None:
            hold_times.append(hold)

        if pnl > 0.01:
            wins += 1
            symbols_seen[sym]["wins"] += 1
        elif pnl < -0.01:
            losses += 1
        else:
            breakeven += 1

        symbols_seen[sym]["pnl"] += pnl
        symbols_seen[sym]["count"] += 1

    avg_hold = sum(hold_times) / len(hold_times) if hold_times else None
    return {
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "total": len(trades),
        "total_pnl": total_pnl,
        "avg_hold": avg_hold,
        "symbols": dict(symbols_seen),
    }


def parse_worker_active(lines: list[str]) -> list[dict]:
    """Extract [ACTIVE] summary lines from worker log."""
    results = []
    for line in lines:
        if "[ACTIVE]" not in line:
            continue
        info = {"raw": line.strip()}
        # Parse equity
        eq_parts = line.split("Eq:$")
        if len(eq_parts) > 1:
            try:
                info["equity"] = float(eq_parts[1].split()[0].strip())
            except (ValueError, IndexError):
                pass
        # Parse active count
        active_parts = line.split("Active:")
        if len(active_parts) > 1:
            try:
                info["active"] = int(active_parts[1].split()[0].strip().rstrip(","))
            except (ValueError, IndexError):
                pass
        # Parse P/L
        pl_parts = line.split("P/L:$")
        if len(pl_parts) > 1:
            try:
                info["session_pnl"] = float(pl_parts[1].split()[0].strip())
            except (ValueError, IndexError):
                pass
        # Parse posture
        if "Posture:" in line:
            post = line.split("Posture:")[-1].strip()
            info["posture"] = post
        results.append(info)
    return results


def parse_worker_price_best(lines: list[str]) -> list[dict]:
    """Extract REV_DIAG lines with price_best info (watchlist candidates)."""
    results = []
    for line in lines:
        if "price_best=" not in line:
            continue
        # Parse price_best=SYMBOL:signal:confidence
        pb = line.split("price_best=")[-1].split()[0]
        parts = pb.split(":")
        if len(parts) >= 3:
            results.append({
                "symbol": parts[0],
                "signal": parts[1],
                "confidence": float(parts[2]),
                "raw": line.strip(),
            })
    return results


def check_watchlist_fired(trades: list[dict], watchlist: list[tuple[str, str]]) -> list[dict]:
    """Check if any watchlist combos appear in recent trades."""
    wl_set = {(s, sig) for s, sig in watchlist}
    hits = []
    for t in trades:
        sym = t.get("symbol", "")
        sig = t.get("entry_signal_type", "")
        if (sym, sig) in wl_set:
            hits.append({
                "symbol": sym,
                "signal": sig,
                "pnl": t.get("realized_pnl", 0.0),
                "time": t.get("exit_time_utc", t.get("entry_time_utc", "?")),
            })
    return hits


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_dashboard_rich(
    now: datetime,
    session: str,
    good_window: bool,
    window_note: str,
    stats: dict,
    worker_active: list[dict],
    price_best: list[dict],
    wl_hits: list[dict],
    worker_lines: list[str],
):
    console = Console()

    # --- Header ---
    time_str = now.strftime("%H:%M:%S UTC")
    session_style = "bold green" if good_window else "bold yellow" if "Late" not in session else "bold red"
    header = Text.assemble(
        ("LONDON LIVE MONITOR  ", "bold white"),
        (time_str, "cyan"),
        ("  |  ", "dim"),
        (session, session_style),
        ("  |  ", "dim"),
        (window_note, "dim white"),
    )
    console.print(Panel(header, border_style="green" if good_window else "yellow"))

    # --- Trade Stats ---
    if stats["total"] > 0:
        pnl_text, pnl_style = color_pnl(stats["total_pnl"])
        wr = (stats["wins"] / stats["total"] * 100) if stats["total"] else 0
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column("Metric", style="dim", width=14)
        table.add_column("Value", justify="right")
        table.add_row("Last", f"{stats['total']} trades")
        table.add_row("W / L / BE", f"{stats['wins']} / {stats['losses']} / {stats['breakeven']}")
        table.add_row("Win Rate", pct_fmt(wr))
        table.add_row("Total P&L", Text(pnl_text, style=pnl_style))
        table.add_row("Avg Hold", fmt_seconds(stats["avg_hold"]) if stats["avg_hold"] else "-")

        # Top symbols
        if stats["symbols"]:
            top_syms = sorted(stats["symbols"].items(), key=lambda x: x[1]["pnl"], reverse=True)[:3]
            sym_parts = []
            for sym, d in top_syms:
                p, s = color_pnl(d["pnl"])
                sym_parts.append(Text(f"{sym} {p} ({d['count']})", style=s))
            table.add_row("Top Symbols", Text.assemble(*[Text("  "), *sym_parts]))

        console.print(table)
    else:
        console.print("[dim]No recent trades.[/dim]")

    # --- Watchlist Hits ---
    if wl_hits:
        wl_table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 2))
        wl_table.add_column("Symbol")
        wl_table.add_column("Signal")
        wl_table.add_column("P&L", justify="right")
        wl_table.add_column("Time", justify="right")
        for h in wl_hits[-5:]:
            p, s = color_pnl(h["pnl"])
            wl_table.add_row(h["symbol"], h["signal"], Text(p, style=s), str(h["time"])[-8:])
        console.print(Panel(wl_table, title="[bold green]:bell: Watchlist Hits", border_style="green", padding=(0, 1)))

    # --- Worker Status ---
    if worker_active:
        latest = worker_active[-1]
        eq = latest.get("equity", 0)
        spnl = latest.get("session_pnl", 0)
        active = latest.get("active", 0)
        posture = latest.get("posture", "?")
        eq_text, eq_style = color_pnl(eq - 69730)  # delta from ~$69,730 baseline
        spnl_text, spnl_style = color_pnl(spnl)
        status_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        status_table.add_column("Key", style="dim", width=10)
        status_table.add_column("Value", justify="right")
        status_table.add_row("Equity", Text(f"${eq:,.2f}", style=eq_style))
        status_table.add_row("Session P/L", Text(spnl_text, style=spnl_style))
        status_table.add_row("Active", str(active))
        status_table.add_row("Posture", posture)
        console.print(status_table)

    # --- Worker Log Tail ---
    last_lines = worker_lines[-5:] if worker_lines else []
    if last_lines:
        console.print("[dim]:scroll: Worker log (last 5):[/dim]")
        for line in last_lines:
            # Colorize key patterns
            text = Text(line)
            if "DEFEND" in line:
                text = Text(line[:80], style="dim yellow")
            elif "price_best" in line:
                text = Text(line[:80], style="dim cyan")
            else:
                text = Text(line[:80], style="dim white")
            console.print(text)


def render_dashboard_plain(
    now: datetime,
    session: str,
    good_window: bool,
    window_note: str,
    stats: dict,
    worker_active: list[dict],
    wl_hits: list[dict],
    worker_lines: list[str],
):
    sep = "=" * 60
    time_str = now.strftime("%H:%M:%S UTC")
    print(f"\n{sep}")
    print(f"  LONDON LIVE MONITOR  {time_str}  |  {session}  |  {window_note}")
    print(sep)

    if stats["total"] > 0:
        wr = stats["wins"] / stats["total"] * 100
        print(f"  Last {stats['total']} trades: {stats['wins']}W / {stats['losses']}L / {stats['breakeven']}BE")
        print(f"  Total P&L: {fmt_pnl(stats['total_pnl'])}  |  Win Rate: {pct_fmt(wr)}  |  Avg Hold: {fmt_seconds(stats['avg_hold'])}")
        if stats["symbols"]:
            top = sorted(stats["symbols"].items(), key=lambda x: x[1]["pnl"], reverse=True)[:3]
            syms = "  ".join(f"{s} {fmt_pnl(d['pnl'])} ({d['count']})" for s, d in top)
            print(f"  Top Symbols: {syms}")
    else:
        print("  No recent trades.")

    if wl_hits:
        print(f"  WATCHLIST HITS ({len(wl_hits)}):")
        for h in wl_hits[-5:]:
            print(f"    {h['symbol']} + {h['signal']}  {fmt_pnl(h['pnl'])}  {str(h['time'])[-8:]}")

    if worker_active:
        latest = worker_active[-1]
        print(f"  Equity: ${latest.get('equity', 0):,.2f}  |  Session P/L: {fmt_pnl(latest.get('session_pnl', 0))}")
        print(f"  Active: {latest.get('active', 0)}  |  Posture: {latest.get('posture', '?')}")

    if worker_lines:
        print(f"  Worker log (last 5):")
        for line in worker_lines[-5:]:
            print(f"    {line[:100]}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_once():
    now = datetime.now(timezone.utc)
    utc_hour = now.hour

    trades = load_recent_trades(TRADE_LOG, n=30)
    worker_lines = load_recent_worker_lines(WORKER_LOG, n=100)

    stats = analyze_trades(trades)
    worker_active = parse_worker_active(worker_lines)
    price_best = parse_worker_price_best(worker_lines)
    wl_hits = check_watchlist_fired(trades, LONDON_WATCHLIST)

    session = get_session_name(utc_hour)
    good, note = is_good_window(utc_hour)

    if RICH:
        render_dashboard_rich(now, session, good, note, stats, worker_active, price_best, wl_hits, worker_lines)
    else:
        render_dashboard_plain(now, session, good, note, stats, worker_active, wl_hits, worker_lines)

    # File status
    missing = []
    if not TRADE_LOG.exists():
        missing.append(str(TRADE_LOG))
    if not WORKER_LOG.exists():
        missing.append(str(WORKER_LOG))
    if missing and not RICH:
        print(f"  [WARN] Missing files: {', '.join(missing)}")
    elif missing:
        from rich.console import Console
        Console().print(f"[yellow]Missing: {', '.join(missing)}[/yellow]")


def main():
    parser = argparse.ArgumentParser(description="London Live Monitor")
    parser.add_argument("--watch", action="store_true", help="Poll every 30 seconds")
    args = parser.parse_args()

    if args.watch:
        print(f"[watch] Polling every {POLL_INTERVAL}s. Ctrl+C to stop.")
        try:
            while True:
                run_once()
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n[watch] Stopped.")
    else:
        run_once()


if __name__ == "__main__":
    main()
