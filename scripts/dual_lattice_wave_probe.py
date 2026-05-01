#!/usr/bin/env python3
"""
DUAL-LATTICE SHADOW PROBE — Wave Cancellation Theory

Tests the hypothesis: running symmetric BUY+SELL lattices on the same symbol
cancels floating P/L while doubling realized profits.

Three configurations tested:
1. Single BUY-only lattice (baseline)
2. Symmetric dual: BUY + SELL with same step (floating should cancel)
3. Asymmetric dual: BUY-tight + SELL-wide (trend-resistant variant)

Output: reports/dual_lattice_probe.csv + reports/dual_lattice_probe.md
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig
from tick_penetration_lattice_core import (
    TickStatefulRearmEngine,
    VOLUME,
    timeframe_seconds,
    tick_pnl_usd,
    pip_size_for,
    spread_price,
    load_ticks_range,
)


ROOT = Path(__file__).resolve().parent.parent
REPORT_CSV = ROOT / "reports" / "dual_lattice_probe.csv"
REPORT_MD = ROOT / "reports" / "dual_lattice_probe.md"

SYMBOLS = ["NAS100", "EURUSD", "GBPUSD", "US30", "XAUUSD"]
DEFAULT_SYMBOL = "NAS100"
DEFAULT_DAYS = 7


@dataclass
class LatticeStats:
    """Track stats for a single lattice."""
    symbol: str
    side: str  # "BUY", "SELL", or "DUAL"
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    max_floating_loss: float = 0.0
    max_floating_profit: float = 0.0
    total_opens: int = 0
    anchor_resets: int = 0
    escape_tier1: int = 0
    escape_tier2: int = 0
    tier3_kills: int = 0


@dataclass
class DualSnapshot:
    """Per-bar snapshot of dual-lattice state."""
    bar_time: str
    price: float
    buy_opens: int
    sell_opens: int
    buy_floating: float
    sell_floating: float
    net_floating: float
    buy_realized: float
    sell_realized: float
    net_realized: float


def build_engine(
    symbol: str,
    timeframe: str,
    step: float,
    step_buy: float | None = None,
    step_sell: float | None = None,
    max_open_per_side: int = 12,
    variant: str = "rearm_lvl2_exc2",
    alpha: float = 0.2,
    cooldown_bars: int = 12,
    momentum_gate: bool = True,
    max_floating_loss: float = -15.0,
    escape_bars: int = 0,
    escape_threshold: float = 0.0,
) -> TickStatefulRearmEngine:
    """Build a TickStatefulRearmEngine with the given geometry."""
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Missing symbol info for {symbol}")

    pip = float(pip_size_for(info) or 0.0)
    spread = float(spread_price(info) or 0.0)

    cfg = RawConfig(
        step_pips=float(step) / pip if pip > 0 else float(step),
        max_open_per_side=max_open_per_side,
        close_mode="two_level",
    )

    from live_penetration_lattice_shadow import REARM_VARIANTS
    variant_obj = REARM_VARIANTS.get(variant)
    if variant_obj is None:
        raise RuntimeError(f"Unknown variant: {variant}")

    engine = TickStatefulRearmEngine(
        symbol,
        cfg,
        info,
        timeframe_name=timeframe,
        variant=variant_obj,
        close_alpha=alpha,
        close_style="all_profitable",
        momentum_gate=momentum_gate,
        cooldown_bars=cooldown_bars,
        sell_gap=1,
        buy_gap=1,
        step_sell=step_sell,
        step_buy=step_buy,
        volume=VOLUME,
        max_floating_loss_usd=max_floating_loss,
        max_lattice_window_bars=240,
        breakout_buffer_pips=5.0,
        escape_bars=escape_bars,
        escape_threshold_usd=escape_threshold,
    )
    return engine


def compute_floating(engine: TickStatefulRearmEngine, tick: dict[str, Any]) -> float:
    """Compute total floating P/L for all open positions."""
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", 0.0))
    total = 0.0
    for t in engine.state.open_tickets:
        direction = t.get("direction", "")
        fill_price = float(t.get("fill_price", 0.0))
        exit_price = bid if direction == "BUY" else ask
        pnl = tick_pnl_usd(engine.symbol, direction, fill_price, exit_price, volume=engine.volume)
        total += pnl
    return total


def run_probe(
    symbol: str,
    timeframe: str,
    days: int,
    step: float,
    step_buy: float | None = None,
    step_sell: float | None = None,
    dual: bool = False,
    dual_asymmetric: bool = False,
    escape_bars: int = 0,
    escape_threshold: float = 0.0,
) -> dict[str, Any]:
    """Run a single or dual-lattice probe over recent tick data."""
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Missing symbol info for {symbol}")

    pip = float(pip_size_for(info) or 0.0)
    spread = float(spread_price(info) or 0.0)
    spread_cost = spread * VOLUME * (info.trade_tick_value or 1.0) if hasattr(info, 'trade_tick_value') else 0.0

    # Load ticks
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    ticks = load_ticks_range(symbol, since, now)
    if not ticks:
        return {"error": f"No ticks loaded for {symbol} over {days} days"}

    # Build engines
    buy_engine = build_engine(
        symbol, timeframe, step, step_buy=step_buy, step_sell=step_sell,
        escape_bars=escape_bars, escape_threshold=escape_threshold,
    )
    buy_stats = LatticeStats(symbol=symbol, side="BUY")

    sell_engine = None
    sell_stats = None
    if dual:
        if dual_asymmetric:
            # SELL-wide: opposite asymmetry
            sell_step = step_sell if step_sell else step * 1.5
            sell_engine = build_engine(
                symbol, timeframe, sell_step,
                step_buy=step_sell, step_sell=step_buy if step_buy else step,
                escape_bars=escape_bars, escape_threshold=escape_threshold,
            )
        else:
            # Symmetric: same geometry
            sell_engine = build_engine(
                symbol, timeframe, step,
                step_buy=step_buy, step_sell=step_sell,
                escape_bars=escape_bars, escape_threshold=escape_threshold,
            )
        sell_stats = LatticeStats(symbol=symbol, side="SELL")

    # Process ticks
    snapshots = []
    tick_counter = 0
    for tick in ticks:
        tick_counter += 1
        buy_engine.process_tick(tick, emit=False)
        buy_stats.total_opens = buy_engine.state.rearm_opens
        buy_stats.realized_closes = buy_engine.state.realized_closes
        buy_stats.realized_net_usd = buy_engine.state.realized_net_usd
        buy_stats.anchor_resets = buy_engine.state.anchor_resets

        buy_float = compute_floating(buy_engine, tick)
        buy_stats.max_floating_loss = min(buy_stats.max_floating_loss, buy_float)
        buy_stats.max_floating_profit = max(buy_stats.max_floating_profit, buy_float)

        if sell_engine and sell_stats:
            sell_engine.process_tick(tick, emit=False)
            sell_stats.total_opens = sell_engine.state.rearm_opens
            sell_stats.realized_closes = sell_engine.state.realized_closes
            sell_stats.realized_net_usd = sell_engine.state.realized_net_usd
            sell_stats.anchor_resets = sell_engine.state.anchor_resets

            sell_float = compute_floating(sell_engine, tick)
            sell_stats.max_floating_loss = min(sell_stats.max_floating_loss, sell_float)
            sell_stats.max_floating_profit = max(sell_stats.max_floating_profit, sell_float)

            # Snapshot every 100 ticks
            if tick_counter % 100 == 0:
                mid = (float(tick.get("bid", 0.0)) + float(tick.get("ask", 0.0))) / 2
                snapshots.append(DualSnapshot(
                    bar_time=datetime.fromtimestamp(int(tick["time"]), tz=timezone.utc).isoformat(),
                    price=mid,
                    buy_opens=len(buy_engine.state.open_tickets),
                    sell_opens=len(sell_engine.state.open_tickets),
                    buy_floating=buy_float,
                    sell_floating=sell_float,
                    net_floating=buy_float + sell_float,
                    buy_realized=buy_engine.state.realized_net_usd,
                    sell_realized=sell_engine.state.realized_net_usd,
                    net_realized=buy_engine.state.realized_net_usd + sell_engine.state.realized_net_usd,
                ))

    # Compute spread costs
    buy_spread_cost = buy_stats.total_opens * spread_cost * 2  # open + close
    sell_spread_cost = (sell_stats.total_opens * spread_cost * 2) if sell_stats else 0.0

    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "days": days,
        "tick_count": len(ticks),
        "step": step,
        "step_buy": step_buy or step,
        "step_sell": step_sell or step,
        "dual": dual,
        "dual_asymmetric": dual_asymmetric,
        "buy": {
            "closes": buy_stats.realized_closes,
            "net_usd": round(buy_stats.realized_net_usd, 2),
            "net_after_spread": round(buy_stats.realized_net_usd - buy_spread_cost, 2),
            "per_close": round(buy_stats.realized_net_usd / max(1, buy_stats.realized_closes), 2),
            "max_floating_loss": round(buy_stats.max_floating_loss, 2),
            "max_floating_profit": round(buy_stats.max_floating_profit, 2),
            "total_opens": buy_stats.total_opens,
            "spread_cost": round(buy_spread_cost, 2),
            "anchor_resets": buy_stats.anchor_resets,
        },
    }

    if sell_stats:
        result["sell"] = {
            "closes": sell_stats.realized_closes,
            "net_usd": round(sell_stats.realized_net_usd, 2),
            "net_after_spread": round(sell_stats.realized_net_usd - sell_spread_cost, 2),
            "per_close": round(sell_stats.realized_net_usd / max(1, sell_stats.realized_closes), 2),
            "max_floating_loss": round(sell_stats.max_floating_loss, 2),
            "max_floating_profit": round(sell_stats.max_floating_profit, 2),
            "total_opens": sell_stats.total_opens,
            "spread_cost": round(sell_spread_cost, 2),
            "anchor_resets": sell_stats.anchor_resets,
        }
        result["dual_combined"] = {
            "net_realized": round(buy_stats.realized_net_usd + sell_stats.realized_net_usd, 2),
            "net_after_spread": round(
                buy_stats.realized_net_usd + sell_stats.realized_net_usd - buy_spread_cost - sell_spread_cost, 2
            ),
            "max_combined_floating": round(
                min(s.net_floating for s in snapshots) if snapshots else 0.0, 2
            ),
            "avg_combined_floating": round(
                sum(s.net_floating for s in snapshots) / max(1, len(snapshots)), 2
            ) if snapshots else 0.0,
        }

    return result


def main():
    parser = argparse.ArgumentParser(description="Dual-lattice wave cancellation probe")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, choices=SYMBOLS)
    parser.add_argument("--timeframe", default="M15", choices=["M1", "M5", "M15", "H1"])
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--step", type=float, default=None, help="Base step (auto from ATR if None)")
    args = parser.parse_args()

    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        return

    try:
        info = mt5.symbol_info(args.symbol)
        if info is None:
            print(f"Symbol {args.symbol} not available")
            return

        pip = float(pip_size_for(info) or 0.0)
        atr = 0.0
        rates = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M15, 0, 500)
        if rates is not None and len(rates) > 0:
            import numpy as np
            highs = np.array([r["high"] for r in rates])
            lows = np.array([r["low"] for r in rates])
            atr = float(np.mean(highs - lows))

        step = args.step or (atr * 2.5)

        print(f"\n{'='*80}")
        print(f"DUAL-LATTICE WAVE CANCELLATION PROBE")
        print(f"Symbol: {args.symbol}, Timeframe: {args.timeframe}, Days: {args.days}")
        print(f"Step: {step:.4f} (ATR: {atr:.4f})")
        print(f"{'='*80}\n")

        # Test 1: Single BUY-only lattice
        print("Testing: Single BUY-only lattice...")
        single = run_probe(args.symbol, args.timeframe, args.days, step)
        print(f"  Closes: {single['buy']['closes']}, Net: ${single['buy']['net_usd']}, $/close: ${single['buy']['per_close']}")
        print(f"  Max floating loss: ${single['buy']['max_floating_loss']}")

        # Test 2: Symmetric dual (BUY + SELL same step)
        print("\nTesting: Symmetric dual (BUY + SELL, same step)...")
        sym_dual = run_probe(args.symbol, args.timeframe, args.days, step, dual=True)
        if "error" not in sym_dual:
            print(f"  BUY:  ${sym_dual['buy']['net_usd']} ({sym_dual['buy']['closes']} closes)")
            print(f"  SELL: ${sym_dual['sell']['net_usd']} ({sym_dual['sell']['closes']} closes)")
            print(f"  COMBINED: ${sym_dual['dual_combined']['net_realized']}")
            print(f"  Avg net floating: ${sym_dual['dual_combined']['avg_combined_floating']}")
            print(f"  Max net floating: ${sym_dual['dual_combined']['max_combined_floating']}")

        # Test 3: Asymmetric dual (BUY-tight + SELL-wide)
        print("\nTesting: Asymmetric dual (BUY-tight + SELL-wide)...")
        asym_dual = run_probe(
            args.symbol, args.timeframe, args.days,
            step, step_buy=step * 0.8, step_sell=step * 1.2,
            dual=True, dual_asymmetric=True,
        )
        if "error" not in asym_dual:
            print(f"  BUY:  ${asym_dual['buy']['net_usd']} ({asym_dual['buy']['closes']} closes)")
            print(f"  SELL: ${asym_dual['sell']['net_usd']} ({asym_dual['sell']['closes']} closes)")
            print(f"  COMBINED: ${asym_dual['dual_combined']['net_realized']}")
            print(f"  Avg net floating: ${asym_dual['dual_combined']['avg_combined_floating']}")
            print(f"  Max net floating: ${asym_dual['dual_combined']['max_combined_floating']}")

        # Write CSV
        rows = []
        for label, data in [("single_buy", single), ("symmetric_dual", sym_dual), ("asymmetric_dual", asym_dual)]:
            if "error" in data:
                continue
            row = {
                "config": label,
                "symbol": data["symbol"],
                "buy_closes": data["buy"]["closes"],
                "buy_net_usd": data["buy"]["net_usd"],
                "buy_per_close": data["buy"]["per_close"],
                "buy_max_floating": data["buy"]["max_floating_loss"],
            }
            if "sell" in data:
                row["sell_closes"] = data["sell"]["closes"]
                row["sell_net_usd"] = data["sell"]["net_usd"]
                row["sell_per_close"] = data["sell"]["per_close"]
                row["sell_max_floating"] = data["sell"]["max_floating_loss"]
                row["combined_net"] = data["dual_combined"]["net_realized"]
                row["combined_after_spread"] = data["dual_combined"]["net_after_spread"]
                row["avg_net_floating"] = data["dual_combined"]["avg_combined_floating"]
                row["max_net_floating"] = data["dual_combined"]["max_combined_floating"]
            rows.append(row)

        REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
            writer.writeheader()
            writer.writerows(rows)

        # Write markdown report
        md_lines = [
            "# Dual-Lattice Wave Cancellation Probe",
            f"\n**Symbol:** {args.symbol} | **Timeframe:** {args.timeframe} | **Days:** {args.days}",
            f"\n**Step:** {step:.4f} | **ATR:** {atr:.4f}",
            f"\n**Generated:** {datetime.now(timezone.utc).isoformat()}",
            "\n## Results Summary\n",
            "| Config | BUY Net $ | SELL Net $ | Combined Net $ | Avg Floating $ | Max Floating $ | BUY $/c | SELL $/c |",
            "|--------|-----------|------------|----------------|----------------|----------------|---------|----------|",
        ]

        for r in rows:
            md_lines.append(
                f"| {r['config']} | ${r['buy_net_usd']} | ${r.get('sell_net_usd', '—')} | "
                f"${r.get('combined_net', '—')} | ${r.get('avg_net_floating', '—')} | "
                f"${r.get('max_net_floating', '—')} | ${r['buy_per_close']} | ${r.get('sell_per_close', '—')} |"
            )

        # Key findings
        md_lines.extend([
            "\n## Key Findings\n",
        ])

        if len(rows) >= 3:
            single_row = rows[0]
            sym_row = rows[1]
            asym_row = rows[2]

            single_net = single_row["buy_net_usd"]
            sym_combined = sym_row.get("combined_net", 0)
            asym_combined = asym_row.get("combined_net", 0)

            sym_ratio = sym_combined / max(1, single_net) if single_net != 0 else 0
            asym_ratio = asym_combined / max(1, single_net) if single_net != 0 else 0

            md_lines.extend([
                f"- **Single BUY baseline:** ${single_net} ({single_row['buy_closes']} closes)",
                f"- **Symmetric dual combined:** ${sym_combined} ({sym_ratio:.2f}× single)",
                f"- **Asymmetric dual combined:** ${asym_combined} ({asym_ratio:.2f}× single)",
                f"- **Symmetric dual avg floating:** ${sym_row.get('avg_net_floating', 0)}",
                f"- **Asymmetric dual avg floating:** ${asym_row.get('avg_net_floating', 0)}",
                "\n### Verdict",
            ])

            if abs(float(sym_row.get("avg_net_floating", 0))) < abs(single_row["buy_max_floating"]) * 0.3:
                md_lines.append("- ✅ **Floating cancellation WORKS** — net floating is significantly lower than single-lattice floating")
            else:
                md_lines.append("- ❌ **Floating cancellation PARTIAL** — net floating is not near zero")

            if sym_ratio > 1.5:
                md_lines.append(f"- ✅ **Profit multiplication WORKS** — dual produces {sym_ratio:.1f}× single-lattice profit")
            elif sym_ratio > 1.0:
                md_lines.append(f"- ⚠️ **Profit increased but not doubled** — dual produces {sym_ratio:.1f}× single")
            else:
                md_lines.append(f"- ❌ **No profit gain** — dual produces {sym_ratio:.1f}× single")

        REPORT_MD.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"\nCSV: {REPORT_CSV}")
        print(f"Report: {REPORT_MD}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
