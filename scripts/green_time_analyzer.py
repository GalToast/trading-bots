"""Green-time analyzer for trade_behavior_log.jsonl.

Usage:
    python scripts/green_time_analyzer.py [--log PATH]

Reads the JSONL trade behavior log and reports on how trades behaved
with respect to going green, going adverse, and ultimately closing.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOTS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG = BOTS_DIR / "trade_behavior_log.jsonl"


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _pct(num: int, denom: int) -> float:
    return (num / denom * 100) if denom else 0.0


def _load(path: Path) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    malformed = 0
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                trades.append(json.loads(raw))
            except json.JSONDecodeError:
                malformed += 1
                continue
    if malformed:
        print(f"  [warn] {malformed} malformed line(s) skipped")
    return trades


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.table import Table

    _RICH = True
    console = Console()

    def section(title: str) -> None:
        console.print(f"\n{'=' * 70}", style="bold cyan")
        console.print(f"  {title}", style="bold cyan")
        console.print(f"{'=' * 70}", style="bold cyan")

    def sub(title: str) -> None:
        console.print(f"\n--- {title} ---", style="bold yellow")

    def row(label: str, value: str) -> None:
        console.print(f"  {label:<45s} {value}")

    def table(headers: list[str], rows: list[list[str]]) -> None:
        t = Table(show_header=True, header_style="bold magenta")
        for h in headers:
            t.add_column(h)
        for r in rows:
            t.add_row(*r)
        console.print(t)

    def good(text: str) -> str:
        return f"[green]{text}[/]"

    def bad(text: str) -> str:
        return f"[red]{text}[/]"

    def warn(text: str) -> str:
        return f"[yellow]{text}[/]"

except ImportError:
    _RICH = False

    def section(title: str) -> None:
        print(f"\n{'=' * 70}")
        print(f"  {title}")
        print(f"{'=' * 70}")

    def sub(title: str) -> None:
        print(f"\n--- {title} ---")

    def row(label: str, value: str) -> None:
        print(f"  {label:<45s} {value}")

    def table(headers: list[str], rows: list[list[str]]) -> None:
        widths = [len(h) for h in headers]
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = max(widths[i], len(c))
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*headers))
        print(fmt.format(*["-" * w for w in widths]))
        for r in rows:
            print(fmt.format(*r))

    def good(text: str) -> str:
        return text

    def bad(text: str) -> str:
        return text

    def warn(text: str) -> str:
        return text


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(trades: list[dict[str, Any]]) -> None:
    total = len(trades)
    if total == 0:
        print("No trades found in log.")
        return

    # -- helpers for safe field access --
    def fnum(t: dict, key: str) -> float | None:
        v = t.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def fstr(t: dict, key: str) -> str:
        return str(t.get(key, ""))

    # ---- OVERVIEW ----
    went_green = [t for t in trades if t.get("first_green_before_fail")]
    never_green = [t for t in trades if not t.get("first_green_before_fail")]

    time_to_green_vals = [
        v for t in went_green if (v := fnum(t, "time_to_first_green_seconds")) is not None
    ]
    time_to_fail_vals = [
        v for t in never_green if (v := fnum(t, "time_to_minus_0_35_atr_seconds")) is not None
    ]
    hit_025_before_035 = sum(1 for t in trades if t.get("hit_0_25_atr_before_minus_0_35_atr"))

    realized_pnls = [fnum(t, "realized_pnl") for t in trades]
    realized_pnls = [p for p in realized_pnls if p is not None]

    section("OVERVIEW")
    row("Total trades", str(total))
    row("Went green", f"{good(str(len(went_green)))} ({_pct(len(went_green), total):.1f}%)")
    row("Never went green", f"{bad(str(len(never_green)))} ({_pct(len(never_green), total):.1f}%)")
    row("Avg time to green (s)", f"{_avg(time_to_green_vals):.1f}" if time_to_green_vals else "N/A")
    row("Avg time to fail/adverse (s)", f"{_avg(time_to_fail_vals):.1f}" if time_to_fail_vals else "N/A")
    row("Hit 0.25 ATR before -0.35 ATR", f"{hit_025_before_035} ({_pct(hit_025_before_035, total):.1f}%)")
    row("Total realized P&L", f"${sum(realized_pnls):.2f}" if realized_pnls else "N/A")
    row("Avg realized P&L", f"${_avg(realized_pnls):.2f}" if realized_pnls else "N/A")

    # ---- BY SYMBOL ----
    section("BY SYMBOL")
    sym_data: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        sym_data[fstr(t, "symbol")].append(t)

    sym_rows: list[list[str]] = []
    for sym in sorted(sym_data):
        st = sym_data[sym]
        n = len(st)
        g = [t for t in st if t.get("first_green_before_fail")]
        tg_vals = [v for t in g if (v := fnum(t, "time_to_first_green_seconds")) is not None]
        g_pnls = [p for t in g if (p := fnum(t, "realized_pnl")) is not None]
        ng_pnls = [p for t in (st - set(g) if False else [t for t in st if not t.get("first_green_before_fail")]) if (p := fnum(t, "realized_pnl")) is not None]
        sym_rows.append([
            sym,
            str(n),
            f"{len(g)} ({_pct(len(g), n):.0f}%)",
            f"{_avg(tg_vals):.0f}s" if tg_vals else "N/A",
            f"${_avg(g_pnls):.2f}" if g_pnls else "N/A",
            f"${_avg(ng_pnls):.2f}" if ng_pnls else "N/A",
        ])
    table(
        ["Symbol", "Trades", "Green%", "Avg Green(s)", "Avg P&L Green", "Avg P&L Not Green"],
        sym_rows,
    )

    # ---- BY SIGNAL TYPE ----
    section("BY SIGNAL TYPE")
    sig_data: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        sig_data[fstr(t, "entry_signal_type")].append(t)

    sig_rows: list[list[str]] = []
    for sig in sorted(sig_data):
        st = sig_data[sig]
        n = len(st)
        g = [t for t in st if t.get("first_green_before_fail")]
        tg_vals = [v for t in g if (v := fnum(t, "time_to_first_green_seconds")) is not None]
        g_pnls = [p for t in g if (p := fnum(t, "realized_pnl")) is not None]
        ng = [t for t in st if not t.get("first_green_before_fail")]
        ng_pnls = [p for t in ng if (p := fnum(t, "realized_pnl")) is not None]
        sig_rows.append([
            sig,
            str(n),
            f"{len(g)} ({_pct(len(g), n):.0f}%)",
            f"{_avg(tg_vals):.0f}s" if tg_vals else "N/A",
            f"${_avg(g_pnls):.2f}" if g_pnls else "N/A",
            f"${_avg(ng_pnls):.2f}" if ng_pnls else "N/A",
        ])
    table(
        ["Signal", "Trades", "Green%", "Avg Green(s)", "Avg P&L Green", "Avg P&L Not Green"],
        sig_rows,
    )

    # ---- BY MODE ----
    section("BY MODE")
    mode_data: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        mode_data[fstr(t, "entry_mode")].append(t)

    mode_rows: list[list[str]] = []
    for mode in sorted(mode_data):
        st = mode_data[mode]
        n = len(st)
        g = [t for t in st if t.get("first_green_before_fail")]
        tg_vals = [v for t in g if (v := fnum(t, "time_to_first_green_seconds")) is not None]
        g_pnls = [p for t in g if (p := fnum(t, "realized_pnl")) is not None]
        ng = [t for t in st if not t.get("first_green_before_fail")]
        ng_pnls = [p for t in ng if (p := fnum(t, "realized_pnl")) is not None]
        mode_rows.append([
            mode,
            str(n),
            f"{len(g)} ({_pct(len(g), n):.0f}%)",
            f"{_avg(tg_vals):.0f}s" if tg_vals else "N/A",
            f"${_avg(g_pnls):.2f}" if g_pnls else "N/A",
            f"${_avg(ng_pnls):.2f}" if ng_pnls else "N/A",
        ])
    table(
        ["Mode", "Trades", "Green%", "Avg Green(s)", "Avg P&L Green", "Avg P&L Not Green"],
        mode_rows,
    )

    # ---- THE 30-SECOND RULE ----
    section("THE 30-SECOND RULE")
    sub("Trades that went green within 30 seconds")
    green_30s = [
        t for t in went_green
        if (v := fnum(t, "time_to_first_green_seconds")) is not None and v <= 30
    ]
    green_30s_won = sum(1 for t in green_30s if (fnum(t, "realized_pnl") or 0) > 0)
    green_30s_lost = len(green_30s) - green_30s_won
    row("Count", str(len(green_30s)))
    row("Ended profitable", f"{good(str(green_30s_won))} ({_pct(green_30s_won, len(green_30s)):.1f}%)")
    row("Ended unprofitable", f"{bad(str(green_30s_lost))} ({_pct(green_30s_lost, len(green_30s)):.1f}%)")

    sub("Trades that went green AFTER 30 seconds")
    green_after_30s = [
        t for t in went_green
        if (v := fnum(t, "time_to_first_green_seconds")) is not None and v > 30
    ]
    after_won = sum(1 for t in green_after_30s if (fnum(t, "realized_pnl") or 0) > 0)
    after_lost = len(green_after_30s) - after_won
    row("Count", str(len(green_after_30s)))
    row("Ended profitable", f"{good(str(after_won))} ({_pct(after_won, len(green_after_30s)):.1f}%)")
    row("Ended unprofitable", f"{bad(str(after_lost))} ({_pct(after_lost, len(green_after_30s)):.1f}%)")

    sub("Quick green vs slow green — win rate comparison")
    fast_rate = _pct(green_30s_won, len(green_30s)) if green_30s else 0
    slow_rate = _pct(after_won, len(green_after_30s)) if green_after_30s else 0
    row("Fast green (<=30s) win rate", f"{fast_rate:.1f}%")
    row("Slow green (>30s)  win rate", f"{slow_rate:.1f}%")
    if fast_rate > slow_rate + 5:
        row("Verdict", warn("Fast greens are noticeably more profitable. Consider cutting trades that don't go green quickly."))
    elif slow_rate > fast_rate + 5:
        row("Verdict", warn("Slow greens are more profitable. Patience may pay off."))
    else:
        row("Verdict", "No major difference — timing to green isn't a strong predictor here.")

    # ---- THE "NEVER GREEN" PROBLEM ----
    section('THE "NEVER GREEN" PROBLEM')
    never_green_count = len(never_green)
    ng_pnls_all = [p for t in never_green if (p := fnum(t, "realized_pnl")) is not None]
    ng_hold_vals = [v for t in never_green if (v := fnum(t, "hold_seconds")) is not None]
    ng_adverse_vals = [v for t in never_green if (v := fnum(t, "time_to_minus_0_35_atr_seconds")) is not None]

    row("Trades that NEVER went green", f"{bad(str(never_green_count))} ({_pct(never_green_count, total):.1f}% of all trades)")
    row("Avg realized P&L", f"{bad(f'${_avg(ng_pnls_all):.2f}')}")
    row("Avg hold time (s)", f"{_avg(ng_hold_vals):.0f}" if ng_hold_vals else "N/A")
    row("Avg time to -0.35 ATR (s)", f"{_avg(ng_adverse_vals):.0f}" if ng_adverse_vals else "N/A (no adverse hit recorded)")
    row("Total P&L from never-green trades", f"{bad(f'${sum(ng_pnls_all):.2f}')}")

    # Breakdown of never-green by symbol
    sub("Never-green breakdown by symbol")
    ng_by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in never_green:
        ng_by_sym[fstr(t, "symbol")].append(t)
    ng_sym_rows: list[list[str]] = []
    for sym in sorted(ng_by_sym, key=lambda s: len(ng_by_sym[s]), reverse=True):
        st = ng_by_sym[sym]
        n = len(st)
        pnls = [p for t in st if (p := fnum(t, "realized_pnl")) is not None]
        ng_sym_rows.append([sym, str(n), f"${_avg(pnls):.2f}", f"${sum(pnls):.2f}"])
    table(
        ["Symbol", "Never-Green", "Avg Loss", "Total Loss"],
        ng_sym_rows,
    )

    # ---- RECOMMENDATIONS ----
    section("RECOMMENDATIONS")
    sub("Worst symbol + signal combos (by green rate)")

    combo_stats: list[tuple[str, str, int, float, float]] = []
    for sym in sorted(sym_data):
        for sig in sorted(sig_data):
            combo_trades = [t for t in trades if fstr(t, "symbol") == sym and fstr(t, "entry_signal_type") == sig]
            if len(combo_trades) < 3:
                continue
            n = len(combo_trades)
            g = sum(1 for t in combo_trades if t.get("first_green_before_fail"))
            gr = _pct(g, n)
            tg_vals = [v for t in combo_trades if t.get("first_green_before_fail") and (v := fnum(t, "time_to_first_green_seconds")) is not None]
            avg_tg = _avg(tg_vals) if tg_vals else float("inf")
            pnls = [p for t in combo_trades if (p := fnum(t, "realized_pnl")) is not None]
            avg_pnl = _avg(pnls) if pnls else 0
            combo_stats.append((sym, sig, n, gr, avg_tg, avg_pnl))  # type: ignore[arg-type]

    # Sort by green rate ascending, then by avg time to green descending
    combo_stats.sort(key=lambda x: (x[3], -x[4]))

    rec_rows: list[list[str]] = []
    block_count = 0
    for sym, sig, n, gr, avg_tg, avg_pnl in combo_stats:
        avg_tg_str = f"{avg_tg:.0f}s" if avg_tg != float("inf") else "N/A (no green)"
        pnl_str = f"${avg_pnl:.2f}"
        if gr < 30 or avg_tg > 300:
            tag = bad("BLOCK")
            block_count += 1
        elif gr < 50:
            tag = warn("WATCH")
        else:
            tag = good("OK")
        rec_rows.append([tag, sym, sig, str(n), f"{gr:.0f}%", avg_tg_str, pnl_str])

    table(
        ["Action", "Symbol", "Signal", "Trades", "Green%", "Avg Green(s)", "Avg P&L"],
        rec_rows,
    )

    row(f"Combos flagged BLOCK", f"{bad(str(block_count))}")
    row(f"Combos flagged WATCH", warn(str(sum(1 for r in rec_rows if r[0] == warn("WATCH")))))

    sub("Summary")
    if block_count:
        blocked = [r for r in rec_rows if r[0] == bad("BLOCK")]
        print()
        for r in blocked:
            print(f"  {bad('BLOCK')} {r[1]} + {r[2]}: {r[4]} green, {r[6]} avg P&L ({r[3]} trades)")
    else:
        row("Verdict", "No symbol+signal combo is a clear loser. No blocks recommended.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log_path = DEFAULT_LOG
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--log", "-l") and i < len(sys.argv) - 1:
            log_path = Path(sys.argv[i + 1])
        elif arg.startswith("--log="):
            log_path = Path(arg.split("=", 1)[1])
        elif arg in ("--help", "-h"):
            print(f"Usage: {sys.argv[0]} [--log PATH]")
            print(f"Default log: {DEFAULT_LOG}")
            return

    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        sys.exit(1)

    print(f"Reading {log_path} ...")
    trades = _load(log_path)
    print(f"Loaded {len(trades)} trades.")
    analyze(trades)


if __name__ == "__main__":
    main()
