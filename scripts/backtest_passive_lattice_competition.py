#!/usr/bin/env python3
"""
Passive Limit-Order Lattice Competition

Competes persistent pre-laid limit-order lattices against active market-triggered
staged entries. Models realistic passive execution friction:

1. Queue position delay — orders don't fill at first touch; must wait in queue
2. Partial fill probability — large moves through levels may only partially fill
3. Gap-through misses — fast moves can skip levels entirely
4. Cancel/replace lag — anchor resets take bars to relocate orders
5. Maker fee economics — lower taker fees but opportunity cost of waiting

Scoring: same as staged_anchor_competition.py
- Primary: realized $/hour
- Universal pass: positive on all tested symbols
- Carry burden: min float, max open, final open
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "passive_lattice_competition.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "passive_lattice_competition.md"

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
}


@dataclass(frozen=True)
class LiveLaneConfig:
    lane_name: str
    kind: str
    symbol: str
    timeframe: str
    step_px: float
    max_open_per_side: int


@dataclass
class PassiveOrder:
    """A resting limit order in the passive lattice."""
    direction: str  # "BUY" or "SELL"
    level: int  # distance from anchor in step units (1, 2, 3, ...)
    price: float
    volume: float = 0.01
    placed_idx: int = 0  # bar index when order was placed
    filled: bool = False
    fill_idx: int = 0
    fill_price: float = 0.0
    # Queue position: how many ticks must pass before this order fills
    queue_position: int = 0
    # Partial fill tracking
    partial_fill_remaining: float = 0.0


@dataclass
class Position:
    """A filled position from a passive order."""
    direction: str
    entry_price: float
    opened_idx: int
    best_price: float = 0.0  # for trailing exit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Passive limit-order lattice competition with realistic fill modeling."
    )
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--anchor-modes",
        nargs="*",
        default=["stable_price", "self_last_fill"],
        help="Anchor modes to test: stable_price, self_last_fill",
    )
    parser.add_argument(
        "--lattice-depths",
        nargs="*",
        type=int,
        default=[5, 10, 15, 20],
        help="How many levels per side to pre-lay",
    )
    parser.add_argument(
        "--queue-delays",
        nargs="*",
        type=int,
        default=[0, 1, 2, 3],
        help="Queue position delay in ticks before fill (0 = immediate, 1+ = must wait)",
    )
    parser.add_argument(
        "--fill-probabilities",
        nargs="*",
        type=float,
        default=[0.7, 0.85, 1.0],
        help="Probability that a touched order actually fills (models partial/gap misses)",
    )
    parser.add_argument(
        "--replace-lags",
        nargs="*",
        type=int,
        default=[0, 1, 2],
        help="Bars of delay when cancel/replacing orders after anchor reset",
    )
    parser.add_argument(
        "--close-modes",
        nargs="*",
        default=[
            "handoff",
            "trail_75",
            "handoff_then_trail_75",
        ],
        help="Profitable-only close families",
    )
    parser.add_argument(
        "--handoff-steps",
        nargs="*",
        type=float,
        default=[0.0, 0.5, 1.0],
    )
    parser.add_argument("--trail-activation-steps", type=float, default=1.0)
    parser.add_argument("--trail-floor-steps", type=float, default=0.25)
    parser.add_argument(
        "--flat-reset-bars",
        nargs="*",
        type=float,
        default=[0.0, 2.0, 4.0],
        help="Reset anchor to flat price after N bars without new fill",
    )
    parser.add_argument(
        "--regenerate-modes",
        nargs="*",
        default=["off", "on"],
        help="Whether filled orders immediately spawn replacements at the same level",
    )
    parser.add_argument("--maker-fee-bps", type=float, default=0.0,
                        help="Maker fee in basis points (0 = same as taker)")
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def _arg_value(args: list[str], key: str, default: str = "") -> str:
    try:
        idx = args.index(key)
    except ValueError:
        return default
    if idx + 1 >= len(args):
        return default
    return str(args[idx + 1])


def load_step_ladder_configs(
    *,
    symbol_filter: set[str] | None = None,
    include_disabled: bool = False,
) -> list[LiveLaneConfig]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    lane_rows = payload.get("lanes") if isinstance(payload, dict) else payload
    if not isinstance(lane_rows, list):
        return []
    rows: list[LiveLaneConfig] = []
    for row in lane_rows:
        lane_kind = str(row.get("kind") or "")
        if lane_kind not in {"live_crypto", "live_crypto_m15"}:
            continue
        enabled_value = row.get("enabled")
        if not include_disabled and enabled_value is False:
            continue
        args = [str(v) for v in (row.get("restart_args") or [])]
        symbol = _arg_value(args, "--symbol")
        if not symbol:
            continue
        if symbol_filter and symbol not in symbol_filter:
            continue
        timeframe = _arg_value(args, "--timeframe", "M15")
        step_px = float(_arg_value(args, "--step", "0") or 0.0)
        max_open = int(
            float(
                _arg_value(
                    args,
                    "--max-open-per-side",
                    _arg_value(args, "--max-open", "0"),
                )
                or 0.0
            )
        )
        if timeframe not in TIMEFRAME_MAP or step_px <= 0.0 or max_open <= 0:
            continue
        rows.append(
            LiveLaneConfig(
                lane_name=symbol,
                kind=lane_kind,
                symbol=symbol,
                timeframe=timeframe,
                step_px=step_px,
                max_open_per_side=max_open,
            )
        )
    return rows


def load_bars(symbol: str, timeframe_name: str, days: int) -> list[dict[str, Any]]:
    timeframe = TIMEFRAME_MAP[timeframe_name]
    bars_per_day = {
        "M1": 1440,
        "M5": 288,
        "M15": 96,
        "H1": 24,
    }[timeframe_name]
    count = bars_per_day * days
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def _segment_path(bar: dict[str, Any]) -> list[float]:
    """Get the price path within a bar for fill simulation."""
    open_px = float(bar["open"])
    high_px = float(bar["high"])
    low_px = float(bar["low"])
    close_px = float(bar["close"])
    # BUY path: open -> low -> high -> close
    # SELL path: open -> high -> low -> close
    if close_px >= open_px:
        return [open_px, low_px, high_px, close_px]
    return [open_px, high_px, low_px, close_px]


def simulate_passive_lattice(
    *,
    cfg: LiveLaneConfig,
    bars: list[dict[str, Any]],
    symbol_info: Any,
    anchor_mode: str,
    lattice_depth: int,
    queue_delay: int,
    fill_probability: float,
    replace_lag: int,
    close_mode: str,
    handoff_steps: float,
    trail_activation_steps: float,
    trail_floor_steps: float,
    maker_fee_bps: float,
    regenerate: bool = False,
    flat_reset_bars: float = 0.0,
    rng_seed: int = 42,
) -> dict[str, Any]:
    """
    Simulate a passive limit-order lattice with realistic fill modeling.

    Key differences from active staged entries:
    - All levels pre-layed around anchor upfront
    - Queue delay before fills (queue_position ticks must pass)
    - Fill probability models partial/gap misses
    - Replace lag when anchor resets
    """
    if not bars:
        return {}

    rng = random.Random(rng_seed)
    step_px = float(cfg.step_px)
    spread_px = float(spread_price(symbol_info))
    maker_fee_multiplier = 1.0 - (maker_fee_bps / 10000.0)  # e.g., 2 bps -> 0.9998

    # State
    anchor = float(bars[0]["close"])
    sell_anchor = float(anchor)
    buy_anchor = float(anchor)

    # Passive order book: resting limit orders
    orders: list[PassiveOrder] = []
    # Filled positions
    positions: list[Position] = []

    # Anchor reset tracking
    anchor_reset_pending = False
    anchor_reset_complete_idx = 0  # bar index when reset completes
    last_fill_idx = 0  # track last fill for flat reset

    # Stats
    stats = {
        "realized_net_usd": 0.0,
        "realized_closes": 0,
        "wins": 0,
        "losses": 0,
        "close_pnls": [],
        "opens": 0,
        "anchor_resets": 0,
        "max_open_total": 0,
        "max_open_buy": 0,
        "max_open_sell": 0,
        "min_floating_usd": 0.0,
        "max_floating_usd": 0.0,
        "final_open_count": 0,
        "trail_closes": 0,
        "handoff_closes": 0,
        "missed_fills": 0,
        "partial_fills": 0,
    }

    def place_initial_orders(current_anchor: float) -> list[PassiveOrder]:
        """Place initial lattice levels around anchor."""
        new_orders = []
        for level in range(1, lattice_depth + 1):
            # SELL orders above anchor
            sell_px = current_anchor + (level * step_px)
            new_orders.append(PassiveOrder(
                direction="SELL", level=level, price=sell_px,
                placed_idx=0, queue_position=queue_delay,
            ))
            # BUY orders below anchor
            buy_px = current_anchor - (level * step_px)
            new_orders.append(PassiveOrder(
                direction="BUY", level=level, price=buy_px,
                placed_idx=0, queue_position=queue_delay,
            ))
        return new_orders

    def relocate_orders(new_anchor: float, current_idx: int) -> None:
        """Cancel and relocate all resting orders after anchor reset."""
        nonlocal anchor_reset_complete_idx
        anchor_reset_complete_idx = current_idx + replace_lag
        stats["anchor_resets"] += 1

        # Remove unfilled orders, create new ones at new anchor
        orders.clear()
        for level in range(1, lattice_depth + 1):
            sell_px = new_anchor + (level * step_px)
            buy_px = new_anchor - (level * step_px)
            # Mark as placed after replace_lag
            orders.append(PassiveOrder(
                direction="SELL", level=level, price=sell_px,
                placed_idx=current_idx + replace_lag,
                queue_position=queue_delay,
            ))
            orders.append(PassiveOrder(
                direction="BUY", level=level, price=buy_px,
                placed_idx=current_idx + replace_lag,
                queue_position=queue_delay,
            ))

    def check_fills(bar: dict[str, Any], idx: int) -> None:
        """Check if resting orders get filled based on price path and queue model."""
        path = _segment_path(bar)
        tick_volume = max(1, int(bar.get("tick_volume", 1)))
        ticks_per_segment = max(1, tick_volume // len(path))

        to_remove = []
        for i, order in enumerate(orders):
            if order.filled:
                continue
            # Order not yet placed (replace lag)
            if idx < order.placed_idx:
                continue

            # Check if price touched order level
            touched = False
            for start, end in zip(path, path[1:]):
                if order.direction == "SELL" and end <= order.price <= start:
                    touched = True
                    break
                elif order.direction == "BUY" and start <= order.price <= end:
                    touched = True
                    break

            if not touched:
                continue

            # Queue delay: must wait queue_position ticks
            order.queue_position -= 1
            if order.queue_position > 0:
                continue

            # Fill probability model
            if rng.random() > fill_probability:
                stats["missed_fills"] += 1
                # Reset queue for next touch
                order.queue_position = queue_delay
                continue

            # Fill the order
            order.filled = True
            order.fill_idx = idx
            order.fill_price = order.price
            stats["opens"] += 1
            last_fill_idx = idx  # track for flat reset

            # Create position
            pos = Position(
                direction=order.direction,
                entry_price=order.price,
                opened_idx=idx,
                best_price=order.price,
            )
            positions.append(pos)

            # Self-regenerative: spawn replacement at same level
            if regenerate:
                orders.append(PassiveOrder(
                    direction=order.direction,
                    level=order.level,
                    price=order.price,
                    placed_idx=idx,  # available immediately but...
                    queue_position=queue_delay + 1,  # ...goes to back of queue
                ))
                stats["partial_fills"] += 1  # track regenerations as "re-fills"

            # Update max open
            buy_count = sum(1 for p in positions if p.direction == "BUY")
            sell_count = sum(1 for p in positions if p.direction == "SELL")
            stats["max_open_total"] = max(stats["max_open_total"], len(positions))
            stats["max_open_buy"] = max(stats["max_open_buy"], buy_count)
            stats["max_open_sell"] = max(stats["max_open_sell"], sell_count)

            to_remove.append(i)

        # Remove filled orders (reverse order to preserve indices)
        for i in reversed(to_remove):
            orders.pop(i)

    def update_best_prices(bar: dict[str, Any]) -> None:
        """Update MFE tracking for all open positions."""
        high = float(bar["high"])
        low = float(bar["low"])
        for pos in positions:
            if pos.direction == "BUY":
                pos.best_price = max(pos.best_price, high)
            else:
                pos.best_price = min(pos.best_price, low)

    def close_handoff(bar: dict[str, Any]) -> None:
        """Close positions when price crosses anchor + handoff threshold."""
        price = float(bar["close"])
        to_keep = []

        for pos in positions:
            if pos.direction == "SELL":
                threshold = anchor - (handoff_steps * step_px)
                should_close = price <= threshold
            else:
                threshold = anchor + (handoff_steps * step_px)
                should_close = price >= threshold

            if not should_close:
                to_keep.append(pos)
                continue

            # Calculate PnL
            if pos.direction == "SELL":
                pnl = unit_pnl_usd(cfg.symbol, "SELL", pos.entry_price, price, spread_px)
            else:
                pnl = unit_pnl_usd(cfg.symbol, "BUY", pos.entry_price, price, spread_px)

            # Apply maker fee adjustment
            pnl *= maker_fee_multiplier

            if pnl > 0:
                stats["realized_net_usd"] += pnl
                stats["realized_closes"] += 1
                stats["wins"] += 1
                stats["handoff_closes"] += 1
                stats["close_pnls"].append(pnl)
            else:
                stats["losses"] += 1
                to_keep.append(pos)  # Don't close losers

        positions.clear()
        positions.extend(to_keep)

    def close_trail(bar: dict[str, Any], retain_ratio: float) -> None:
        """Trailing stop exit for positions."""
        price = float(bar["close"])
        activation_px = trail_activation_steps * step_px
        floor_px = trail_floor_steps * step_px
        to_keep = []

        for pos in positions:
            # Update best price
            if pos.direction == "BUY":
                pos.best_price = max(pos.best_price, float(bar["high"]))
            else:
                pos.best_price = min(pos.best_price, float(bar["low"]))

            # Calculate MFE
            if pos.direction == "BUY":
                mfe = pos.best_price - pos.entry_price
            else:
                mfe = pos.entry_price - pos.best_price

            if mfe < activation_px:
                to_keep.append(pos)
                continue

            # Trailing stop
            retained = max(floor_px, mfe * retain_ratio)
            if pos.direction == "BUY":
                stop_price = pos.entry_price + retained
                should_close = price <= stop_price
                exit_price = stop_price if should_close else price
            else:
                stop_price = pos.entry_price - retained
                should_close = price >= stop_price
                exit_price = stop_price if should_close else price

            if not should_close:
                to_keep.append(pos)
                continue

            pnl = unit_pnl_usd(cfg.symbol, pos.direction, pos.entry_price, exit_price, spread_px)
            pnl *= maker_fee_multiplier

            if pnl > 0:
                stats["realized_net_usd"] += pnl
                stats["realized_closes"] += 1
                stats["wins"] += 1
                stats["trail_closes"] += 1
                stats["close_pnls"].append(pnl)
            else:
                stats["losses"] += 1
                to_keep.append(pos)

        positions.clear()
        positions.extend(to_keep)

    def close_hybrid(bar: dict[str, Any], retain_ratio: float) -> None:
        """Hybrid: handoff first, then trail remainder."""
        # First pass: handoff
        close_handoff(bar)
        # Second pass: trail remaining
        close_trail(bar, retain_ratio)

    def _parse_close_mode(mode: str):
        """Parse close mode string into (type, params)."""
        if mode == "handoff":
            return ("handoff", None)
        elif mode.startswith("trail_"):
            ratio = float(mode.split("_")[1]) / 100.0
            return ("trail", ratio)
        elif mode.startswith("handoff_then_trail_"):
            ratio = float(mode.split("_")[-1]) / 100.0
            return ("hybrid", ratio)
        return ("handoff", None)

    # === Main simulation loop ===
    orders = place_initial_orders(anchor)

    for idx, bar in enumerate(bars):
        # Anchor mode updates
        if anchor_mode == "self_last_fill":
            # Reset anchor to last close price
            anchor = float(bar["close"])
            sell_anchor = anchor
            buy_anchor = anchor
            # Trigger relocate if anchor moved significantly
            # Simple rule: relocate every 10 bars for self_last_fill
            if idx % 10 == 0 and idx > 0:
                relocate_orders(anchor, idx)

        # Check fills
        check_fills(bar, idx)

        # Flat reset: if no fills for flat_reset_bars, snap anchor to current close
        if flat_reset_bars > 0 and idx > last_fill_idx + flat_reset_bars:
            new_anchor = float(bar["close"])
            if abs(new_anchor - anchor) > step_px * 0.1:  # only reset if moved meaningfully
                anchor = new_anchor
                sell_anchor = anchor
                buy_anchor = anchor
                relocate_orders(anchor, idx)
                last_fill_idx = idx  # reset the counter

        # Update MFE tracking
        update_best_prices(bar)

        # Close logic
        close_type, close_param = _parse_close_mode(close_mode)
        if close_type == "handoff":
            close_handoff(bar)
        elif close_type == "trail":
            close_trail(bar, close_param)
        elif close_type == "hybrid":
            close_hybrid(bar, close_param)

        # Track floating PnL
        if positions:
            price = float(bar["close"])
            floating = []
            for pos in positions:
                pnl = unit_pnl_usd(cfg.symbol, pos.direction, pos.entry_price, price, spread_px)
                floating.append(pnl)
            net_floating = sum(floating)
            stats["min_floating_usd"] = min(stats["min_floating_usd"], net_floating)
            stats["max_floating_usd"] = max(stats["max_floating_usd"], net_floating)

    # Final stats
    stats["final_open_count"] = len(positions)

    # Calculate time-weighted metrics
    if len(bars) > 1:
        first_time = bars[0]["time"]
        last_time = bars[-1]["time"]
        hours = (last_time - first_time) / 3600.0
        if hours > 0:
            stats["realized_per_hour"] = stats["realized_net_usd"] / hours
        else:
            stats["realized_per_hour"] = 0.0
    else:
        stats["realized_per_hour"] = 0.0

    return stats


def _run_single_task(cfg, bars, info, params, rng_seed) -> list[dict]:
    """Run one parameter combination and return results."""
    stats = simulate_passive_lattice(
        cfg=cfg,
        bars=bars,
        symbol_info=info,
        **params,
        rng_seed=rng_seed,
    )
    
    if not stats:
        return []
    
    realized_per_hour = stats.get("realized_per_hour", 0.0)
    realized_net = stats["realized_net_usd"]
    closes = stats["realized_closes"]
    min_float = stats["min_floating_usd"]
    max_open = stats["max_open_total"]
    final_open = stats["final_open_count"]
    wins = stats["wins"]
    losses = stats["losses"]
    missed = stats["missed_fills"]
    partials = stats["partial_fills"]
    resets = stats["anchor_resets"]
    universal_pass = realized_net > 0 and closes > 0
    
    return [{
        "symbol": cfg.symbol,
        "timeframe": cfg.timeframe,
        "step_px": cfg.step_px,
        "anchor": params["anchor_mode"],
        "depth": params["lattice_depth"],
        "queue_delay": params["queue_delay"],
        "fill_probability": params["fill_probability"],
        "replace_lag": params["replace_lag"],
        "regenerate": params["regenerate"],
        "close_mode": params["close_mode"],
        "handoff_steps": params["handoff_steps"],
        "realized_per_hour": round(realized_per_hour, 4),
        "realized_net_usd": round(realized_net, 2),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "min_floating_usd": round(min_float, 2),
        "max_open_total": max_open,
        "final_open_count": final_open,
        "missed_fills": missed,
        "partial_fills": partials,
        "anchor_resets": resets,
        "universal_pass": universal_pass,
    }]


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MT5 initialization failed")
        return 1

    # Load configs
    symbol_filter = set(args.symbols) if args.symbols else None
    configs = load_step_ladder_configs(symbol_filter=symbol_filter)

    if not configs:
        print("No matching lattice configs found in registry")
        mt5.shutdown()
        return 1

    # Deduplicate by symbol (we only need one config per symbol for passive lattice)
    seen_symbols: dict[str, LiveLaneConfig] = {}
    for cfg in configs:
        if cfg.symbol not in seen_symbols:
            seen_symbols[cfg.symbol] = cfg
    configs = list(seen_symbols.values())

    print(f"Testing {len(configs)} symbols: {[c.symbol for c in configs]}")
    print(f"Days: {args.days}")
    print(f"Anchor modes: {args.anchor_modes}")
    print(f"Lattice depths: {args.lattice_depths}")
    print(f"Queue delays: {args.queue_delays}")
    print(f"Fill probabilities: {args.fill_probabilities}")
    print(f"Replace lags: {args.replace_lags}")
    print(f"Regenerate modes: {args.regenerate_modes}")
    print(f"Close modes: {args.close_modes}")

    # Build task list and cache bar/info data
    rows = []
    tasks = []
    bars_map: dict[str, list] = {}
    info_map: dict[str, Any] = {}
    
    for cfg in configs:
        symbol = cfg.symbol
        bars = load_bars(symbol, cfg.timeframe, args.days)
        if not bars:
            print(f"  No bars for {symbol}")
            continue

        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"  No symbol info for {symbol}")
            continue

        bars_map[symbol] = bars
        info_map[symbol] = info

        print(f"\n{'='*80}")
        print(f"=== {symbol} ({cfg.timeframe}, step={cfg.step_px}) ===")
        print(f"{'='*80}")

        for anchor_mode in args.anchor_modes:
            for depth in args.lattice_depths:
                for queue_delay in args.queue_delays:
                    for fill_prob in args.fill_probabilities:
                        for replace_lag in args.replace_lags:
                            for flat_reset in args.flat_reset_bars:
                                for regenerate in args.regenerate_modes:
                                    for close_mode in args.close_modes:
                                        for handoff in args.handoff_steps:
                                            tasks.append((
                                                cfg, anchor_mode, depth, queue_delay,
                                                fill_prob, replace_lag, flat_reset, regenerate,
                                                close_mode, handoff,
                                            ))

    print(f"\nRunning {len(tasks)} parameter combinations...")
    
    # Run serially (MT5 is not thread-safe)
    rng_counter = 0
    for i, (cfg, anchor_mode, depth, queue_delay, fill_prob, replace_lag, flat_reset, regenerate, close_mode, handoff) in enumerate(tasks):
        params = {
            "anchor_mode": anchor_mode,
            "lattice_depth": depth,
            "queue_delay": queue_delay,
            "fill_probability": fill_prob,
            "replace_lag": replace_lag,
            "close_mode": close_mode,
            "handoff_steps": handoff,
            "trail_activation_steps": args.trail_activation_steps,
            "trail_floor_steps": args.trail_floor_steps,
            "maker_fee_bps": args.maker_fee_bps,
            "regenerate": (regenerate == "on"),
            "flat_reset_bars": flat_reset,
        }
        result = _run_single_task(cfg, bars_map[cfg.symbol], info_map[cfg.symbol], params, rng_counter)
        rows.extend(result)
        rng_counter += 1
        
        if (i + 1) % 50 == 0 or (i + 1) == len(tasks):
            completed_regen = sum(1 for r in rows if r.get("regenerate") == "on")
            print(f"  Progress: {i+1}/{len(tasks)} tasks completed, {len(rows)} results, {completed_regen} regen-ON")

    # Sort by realized $/h descending
    rows.sort(key=lambda r: r["realized_per_hour"], reverse=True)

    # Write CSV
    if rows:
        output_csv = Path(args.output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nSaved {output_csv}")

        # Write Markdown summary
        output_md = Path(args.output_md)
        with output_md.open("w", encoding="utf-8") as f:
            f.write("# Passive Limit-Order Lattice Competition\n\n")
            f.write(f"- Days: `{args.days}`\n")
            f.write(f"- Objective: compete pre-laid passive lattices against active staged entries\n")
            f.write(f"- Tested symbols: `{', '.join(sorted(set(r['symbol'] for r in rows)))}`\n")
            f.write(f"- Universal pass rule: positive realized on every tested symbol\n\n")

            # Top 20 rows
            f.write("## Top 20 Rows by Realized $/Hour\n\n")
            f.write("| Anchor | Depth | Queue | FillProb | Lag | Regen | Close | Handoff | $/h | Realized | Closes | Min Float | Max Open | Final Open | Pass |\n")
            f.write("| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n")

            for r in rows[:20]:
                f.write(
                    f"| `{r['anchor']}` | {r['depth']} | {r['queue_delay']} | "
                    f"{r['fill_probability']:.2f} | {r['replace_lag']} | "
                    f"{'ON' if r['regenerate'] == 'on' else 'OFF'} | "
                    f"`{r['close_mode']}` | {r['handoff_steps']:.1f} | "
                    f"${r['realized_per_hour']:+.4f} | ${r['realized_net_usd']:+.2f} | "
                    f"{r['closes']} | ${r['min_floating_usd']:+.2f} | "
                    f"{r['max_open_total']} | {r['final_open_count']} | "
                    f"{r['universal_pass']} |\n"
                )

            # Symbol breakdown
            f.write("\n## Per-Symbol Breakdown\n\n")
            for symbol in sorted(set(r["symbol"] for r in rows)):
                sym_rows = [r for r in rows if r["symbol"] == symbol]
                if not sym_rows:
                    continue
                best = sym_rows[0]  # already sorted by $/h
                f.write(f"### {symbol}\n\n")
                f.write(f"- Best row: `{best['anchor']}` / depth={best['depth']} / "
                        f"regen={'ON' if best['regenerate'] == 'on' else 'OFF'} / "
                        f"`{best['close_mode']}` / handoff {best['handoff_steps']} "
                        f"-> `${best['realized_per_hour']:+.4f}/h`, realized `${best['realized_net_usd']:+.2f}`, "
                        f"closes {best['closes']}, min floating `${best['min_floating_usd']:+.2f}`, "
                        f"max open {best['max_open_total']}\n\n")

                # Top 5 for this symbol
                f.write("| Anchor | Depth | Queue | FillProb | Lag | Regen | Close | Handoff | $/h | Realized | Closes | Min Float | Max Open | Final Open |\n")
                f.write("| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n")
                for r in sym_rows[:5]:
                    f.write(
                        f"| `{r['anchor']}` | {r['depth']} | {r['queue_delay']} | "
                        f"{r['fill_probability']:.2f} | {r['replace_lag']} | "
                        f"{'ON' if r['regenerate'] == 'on' else 'OFF'} | "
                        f"`{r['close_mode']}` | {r['handoff_steps']:.1f} | "
                        f"${r['realized_per_hour']:+.4f} | ${r['realized_net_usd']:+.2f} | "
                        f"{r['closes']} | ${r['min_floating_usd']:+.2f} | "
                        f"{r['max_open_total']} | {r['final_open_count']} |\n"
                    )
                f.write("\n")

        print(f"Saved {output_md}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
