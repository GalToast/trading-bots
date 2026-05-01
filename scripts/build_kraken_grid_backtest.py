#!/usr/bin/env python3
"""
Grid Trading Backtest for Kraken Spot.

Grid trading works by:
1. Placing buy orders at descending price levels (the grid)
2. Placing corresponding sell orders above each buy level
3. When price drops, buy orders fill. When it bounces, sell orders fill
4. Profit from oscillations within the grid range

This is fundamentally DIFFERENT from momentum:
- Momentum: buy after UP, sell at target (catches falling knives)
- Grid: buy on DOWN, sell on UP (profits from mean reversion)

Key: grid trading works BEST in ranging/oscillating markets and LOSES in strong trends.
The wicks in the candles represent where orders actually filled.

Usage:
    python scripts/build_kraken_grid_backtest.py
    python scripts/build_kraken_grid_backtest.py --products BILLY-USD,CLOUD-USD --grid-spacing-bps 200 --num-levels 10
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
CACHE = ROOT / "reports" / "cache"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

DEFAULT_CACHE_PATH = CACHE / "kraken_ohlc_collector.json"
DEFAULT_PRODUCTS = "SHAPE-USD,SWEAT-USD,HONEY-USD,CQT-USD,BILLY-USD,DUCK-USD,CLOUD-USD"
DEFAULT_GRID_SPACING_BPS = 200.0  # 2% between grid levels
DEFAULT_NUM_LEVELS = 10
DEFAULT_INITIAL_CAPITAL = 100.0
DEFAULT_MAKER_FEE_BPS = 16.0  # Kraken tier 0 maker fee
DEFAULT_TAKER_FEE_BPS = 120.0


@dataclass
class GridPosition:
    level: int  # Grid level (0 = highest price, N = lowest)
    buy_price: float
    buy_fee: float
    buy_time: float
    sell_price: float | None = None
    sell_fee: float | None = None
    sell_time: float | None = None
    pnl_bps: float | None = None  # P&L in basis points

    @property
    def is_open(self) -> bool:
        return self.sell_price is None

    @property
    def net_pnl_bps(self) -> float:
        if self.pnl_bps is None:
            return 0.0
        # Subtract round-trip fees
        fee_bps = self.buy_fee / self.buy_price * 10000 if self.buy_price > 0 else 0
        fee_bps += (self.sell_fee or 0) / self.sell_price * 10000 if self.sell_price else 0
        return self.pnl_bps - fee_bps


@dataclass
class GridState:
    levels: list[float]  # Price levels from highest to lowest
    positions: list[GridPosition] = field(default_factory=list)
    realized_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    open_positions: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_candles(cache_path: Path) -> dict[str, dict[str, list]]:
    """Load candle cache."""
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    products = cache.get("products", {})
    result = {}
    for product, prod_data in products.items():
        grains = {}
        for grain_str, grain_data in prod_data.get("granularities", {}).items():
            grains[int(grain_str)] = grain_data.get("candles", [])
        if grains:
            result[product] = grains
    return result


def build_grid_levels(current_price: float, spacing_bps: float, num_levels: int) -> list[float]:
    """
    Build grid levels centered around current price.
    Level 0 = current_price (or slightly above)
    Level N = current_price - N * spacing
    """
    spacing = spacing_bps / 10000.0
    levels = []
    for i in range(num_levels):
        level_price = current_price * (1 - i * spacing)
        levels.append(level_price)
    return levels


def simulate_grid(
    candles: list[dict],
    grid_spacing_bps: float,
    num_levels: int,
    initial_capital: float,
    maker_fee_bps: float,
    taker_fee_bps: float,
) -> dict[str, Any]:
    """
    Simulate grid trading on a candle series.

    Execution model:
    - When candle LOW touches a grid level → BUY fills (maker order at that level)
    - When candle HIGH touches the sell level (one level above filled buy) → SELL fills
    - Fees: maker fee on entry (16bps), taker fee on exit (120bps, assuming we need to exit quickly)

    This models a realistic grid bot behavior where limit orders sit at each level
    and get filled when price wicks through them.
    """
    if len(candles) < 10:
        return {"error": "Not enough candles"}

    # Initialize grid around first candle's close
    first_price = candles[0]["c"]
    grid_levels = build_grid_levels(first_price, grid_spacing_bps, num_levels)

    state = GridState(
        levels=grid_levels,
        positions=[],
        realized_pnl_usd=0.0,
        total_fees_usd=0.0,
        open_positions=0,
    )

    cash = initial_capital
    position_size_usd = initial_capital / num_levels  # Equal allocation per level

    for idx, candle in enumerate(candles):
        high = candle["h"]
        low = candle["l"]
        close = candle["c"]

        # Check for buy fills (low touches grid level)
        for level_idx, level_price in enumerate(grid_levels):
            if low <= level_price:
                # Check if this level is already filled (has open position)
                already_filled = any(
                    p.level == level_idx and p.is_open
                    for p in state.positions
                )
                if not already_filled:
                    # Buy fills at grid level (maker order)
                    buy_fee = position_size_usd * maker_fee_bps / 10000.0
                    position = GridPosition(
                        level=level_idx,
                        buy_price=level_price,
                        buy_fee=buy_fee,
                        buy_time=candle["t"],
                    )
                    state.positions.append(position)
                    cash -= position_size_usd + buy_fee
                    state.open_positions += 1

        # Check for sell fills (high touches sell level = one level above filled buy)
        for position in state.positions:
            if position.is_open:
                sell_level = grid_levels[position.level - 1] if position.level > 0 else grid_levels[0] * (1 + grid_spacing_bps / 10000.0)
                if high >= sell_level:
                    # Sell fills
                    position.sell_price = sell_level
                    sell_fee = position_size_usd * taker_fee_bps / 10000.0  # Assume taker exit
                    position.sell_fee = sell_fee
                    position.sell_time = candle["t"]

                    # Calculate P&L
                    gross_pnl = position_size_usd * (sell_level / position.buy_price - 1)
                    position.pnl_bps = (sell_level / position.buy_price - 1) * 10000.0

                    state.realized_pnl_usd += gross_pnl
                    state.total_fees_usd += position.buy_fee + sell_fee
                    cash += position_size_usd + gross_pnl - sell_fee
                    state.open_positions -= 1

    # Close all remaining positions at last candle close (force exit)
    last_close = candles[-1]["c"]
    for position in state.positions:
        if position.is_open:
            position.sell_price = last_close
            sell_fee = position_size_usd * taker_fee_bps / 10000.0
            position.sell_fee = sell_fee
            position.sell_time = candles[-1]["t"]
            position.pnl_bps = (last_close / position.buy_price - 1) * 10000.0

            gross_pnl = position_size_usd * (last_close / position.buy_price - 1)
            state.realized_pnl_usd += gross_pnl
            state.total_fees_usd += position.buy_fee + sell_fee
            cash += position_size_usd + gross_pnl - sell_fee
            state.open_positions -= 1

    # Summary stats
    total_trades = len(state.positions)
    winning_trades = [p for p in state.positions if p.pnl_bps and p.pnl_bps > 0]
    losing_trades = [p for p in state.positions if p.pnl_bps and p.pnl_bps <= 0]

    net_pnl_usd = state.realized_pnl_usd - state.total_fees_usd
    net_return_pct = (net_pnl_usd / initial_capital) * 100

    return {
        "grid_spacing_bps": grid_spacing_bps,
        "num_levels": num_levels,
        "initial_capital": initial_capital,
        "final_cash": round(cash, 4),
        "realized_pnl_usd": round(state.realized_pnl_usd, 4),
        "total_fees_usd": round(state.total_fees_usd, 4),
        "net_pnl_usd": round(net_pnl_usd, 4),
        "net_return_pct": round(net_return_pct, 4),
        "total_trades": total_trades,
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate_pct": round(len(winning_trades) / total_trades * 100, 1) if total_trades else 0,
        "avg_trade_pnl_bps": round(
            sum(p.pnl_bps for p in state.positions if p.pnl_bps) / total_trades, 2
        ) if total_trades else 0,
        "max_open_positions": max(
            sum(1 for p in state.positions[:i] if p.is_open)
            for i in range(1, len(state.positions) + 1)
        ) if state.positions else 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest grid trading on Kraken spot candles")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--grid-spacing-bps", type=float, default=DEFAULT_GRID_SPACING_BPS)
    parser.add_argument("--num-levels", type=int, default=DEFAULT_NUM_LEVELS)
    parser.add_argument("--initial-capital", type=float, default=DEFAULT_INITIAL_CAPITAL)
    parser.add_argument("--maker-fee-bps", type=float, default=DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--json-path", default=str(REPORTS / "kraken_grid_backtest.json"))
    parser.add_argument("--md-path", default=str(REPORTS / "kraken_grid_backtest.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"📊 Kraken Grid Trading Backtest")
    print(f"   Products: {products}")
    print(f"   Grid spacing: {args.grid_spacing_bps}bps")
    print(f"   Levels: {args.num_levels}")
    print(f"   Capital: ${args.initial_capital}")
    print(f"   Maker fee: {args.maker_fee_bps}bps, Taker fee: {args.taker_fee_bps}bps")
    print()

    candles = load_candles(Path(args.cache_path))
    all_results = {}

    for product in products:
        prod_candles = candles.get(product, {})
        if not prod_candles:
            print(f"  ❌ {product}: no candles found")
            continue

        # Use 5m candles
        if 5 in prod_candles:
            grain = 5
            grain_name = "5m"
        elif 1 in prod_candles:
            grain = 1
            grain_name = "1m"
        else:
            grain = list(prod_candles.keys())[0]
            grain_name = f"{grain}m"

        c = prod_candles[grain]
        print(f"  Testing {product} ({grain_name}, {len(c)} candles)...", end=" ", flush=True)

        result = simulate_grid(
            candles=c,
            grid_spacing_bps=args.grid_spacing_bps,
            num_levels=args.num_levels,
            initial_capital=args.initial_capital,
            maker_fee_bps=args.maker_fee_bps,
            taker_fee_bps=args.taker_fee_bps,
        )

        all_results[product] = result

        if "error" in result:
            print(f"❌ {result['error']}")
        else:
            print(f"${result['net_pnl_usd']:+.4f} net, {result['win_rate_pct']:.0f}% WR, {result['total_trades']} trades")

    # Save JSON
    payload = {
        "generated_at": utc_now_iso(),
        "parameters": {
            "products": products,
            "grid_spacing_bps": args.grid_spacing_bps,
            "num_levels": args.num_levels,
            "initial_capital": args.initial_capital,
            "maker_fee_bps": args.maker_fee_bps,
            "taker_fee_bps": args.taker_fee_bps,
        },
        "results": all_results,
    }
    Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    # Write MD report
    md_lines = [
        "# Kraken Grid Trading Backtest",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Grid spacing: {args.grid_spacing_bps}bps between levels",
        f"- Levels: {args.num_levels}",
        f"- Capital: ${args.initial_capital}",
        f"- Maker fee: {args.maker_fee_bps}bps (entry), Taker fee: {args.taker_fee_bps}bps (exit)",
        "",
        "## Results by Product",
        "",
        "| Product | Net P&L ($) | Net Return (%) | Win Rate | Trades | Avg Trade (bps) | Max Open Positions |",
        "|---------|------------:|---------------:|---------:|-------:|----------------:|-------------------:|",
    ]

    for product in products:
        r = all_results.get(product, {})
        if r and "error" not in r:
            md_lines.append(
                f"| {product} | ${r['net_pnl_usd']:+.4f} | {r['net_return_pct']:+.2f}% | {r['win_rate_pct']:.0f}% | {r['total_trades']} | {r['avg_trade_pnl_bps']:+.2f}bps | {r['max_open_positions']} |"
            )

    md_lines.extend([
        "",
        "## How Grid Trading Works",
        "",
        "1. **Grid levels** are set below current price at fixed intervals (e.g., every 200bps)",
        "2. **Buy orders** sit at each level (maker orders, 16bps fee)",
        "3. When candle LOW touches a level → buy fills",
        "4. **Sell orders** are placed one level above each filled buy",
        "5. When candle HIGH touches a sell level → sell fills (taker, 120bps fee)",
        "6. **Profit** comes from oscillations: buy low, sell higher",
        "",
        "## Key Insight",
        "",
        "- Grid trading works BEST in ranging/oscillating markets",
        "- Grid trading LOSES in strong downtrends (bags accumulate at lower levels)",
        "- The wider the grid spacing, the fewer trades but more profit per trade",
        "- Maker entry fees are low (16bps), but taker exit fees are high (120bps)",
        "",
    ])

    # Find winners
    winners = [(p, r) for p, r in all_results.items() if r.get("net_pnl_usd", 0) > 0]
    losers = [(p, r) for p, r in all_results.items() if r.get("net_pnl_usd", 0) <= 0]

    if winners:
        md_lines.append(f"**{len(winners)} profitable configurations:**")
        md_lines.append("")
        for p, r in sorted(winners, key=lambda x: x[1]["net_pnl_usd"], reverse=True):
            md_lines.append(f"- {p}: ${r['net_pnl_usd']:+.4f} ({r['win_rate_pct']:.0f}% WR, {r['total_trades']} trades)")
        md_lines.append("")

    if losers:
        md_lines.append(f"**{len(losers)} losing configurations:**")
        md_lines.append("")
        for p, r in sorted(losers, key=lambda x: x[1]["net_pnl_usd"]):
            md_lines.append(f"- {p}: ${r['net_pnl_usd']:+.4f} ({r['win_rate_pct']:.0f}% WR, {r['total_trades']} trades)")
        md_lines.append("")

    Path(args.md_path).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"\n📁 JSON: {args.json_path}")
    print(f"📁 MD: {args.md_path}")


if __name__ == "__main__":
    main()
