"""Escape Hatch Module — surgical exit from negative/stale lattice positions.

Three tiers:
  Tier 1: Breakeven escape — close positions open >N bars at ~$0 cost
  Tier 2: Extreme escape — cut worst 1-2 positions at defined loss threshold
  Tier 3: Full kill — existing max_floating_loss (not handled here)

Usage from runner:
  from escape_hatch import run_escape_hatch
  run_escape_hatch(engines, action_sink, event_path, runner_status, escape_config)
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

import MetaTrader5 as mt5


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_position_pnl_usd(pos) -> float:
    """Compute floating PnL in USD for a broker position."""
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return 0.0
    if pos.type == 0:  # BUY
        return (tick.bid - pos.price_open) * pos.volume * 100000 / 100
    else:  # SELL
        return (pos.price_open - tick.ask) * pos.volume * 100000 / 100


def _close_position_via_sink(action_sink, pos, comment: str = "ESCAPE_HATCH") -> bool:
    """Close a position using the runner's action sink (direct-live aware)."""
    if action_sink is not None:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == 0 else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE
    return False


def find_stale_unprofitable_positions(engines, max_bars: int = 20) -> list[dict]:
    """Find positions open for >max_bars that aren't profitable.
    
    These are candidates for Tier 1 breakeven escape.
    """
    mt5.initialize()
    all_positions = mt5.positions_get()
    mt5.shutdown()
    if all_positions is None:
        return []

    stale = []
    for pos in all_positions:
        # Check if this position belongs to one of our engines
        engine = engines.get(pos.symbol)
        if engine is None:
            continue

        pnl = _compute_position_pnl_usd(pos)
        # Position is unprofitable and has been open a while
        # We estimate bars from position open time vs engine's last bar time
        engine_bar_time = getattr(engine.state, 'last_bar_time', 0) or 0
        if engine_bar_time > 0 and pos.time > 0:
            # Approximate bars open (M15 = 900 seconds)
            timeframe_seconds = 900  # M15 default
            if hasattr(engine, 'timeframe_name') and engine.timeframe_name:
                tf = str(engine.timeframe_name).upper()
                if 'M1' in tf: timeframe_seconds = 60
                elif 'M5' in tf: timeframe_seconds = 300
                elif 'M15' in tf: timeframe_seconds = 900
                elif 'H1' in tf: timeframe_seconds = 3600
                elif 'H4' in tf: timeframe_seconds = 14400
            bars_open = max(0, int((engine_bar_time - pos.time) / timeframe_seconds))
            if bars_open > max_bars and pnl < 0:
                stale.append({
                    'pos': pos,
                    'pnl': pnl,
                    'bars_open': bars_open,
                    'tier': 'breakeven',
                })

    return stale


def find_extreme_negative_positions(engines, cut_count: int = 1, max_loss: float = 5.0) -> list[dict]:
    """Find the worst positions at grid extremes.
    
    These are candidates for Tier 2 surgical escape.
    """
    mt5.initialize()
    all_positions = mt5.positions_get()
    mt5.shutdown()
    if all_positions is None:
        return []

    candidates = []
    for pos in all_positions:
        engine = engines.get(pos.symbol)
        if engine is None:
            continue

        pnl = _compute_position_pnl_usd(pos)
        if pnl < -max_loss:
            candidates.append({
                'pos': pos,
                'pnl': pnl,
                'tier': 'extreme',
            })

    # Sort worst first, take top cut_count
    candidates.sort(key=lambda x: x['pnl'])
    return candidates[:cut_count]


def execute_escape_hatch(
    engines: dict,
    action_sink: Any,
    event_path: Any,
    escape_config: dict,
    dry_run: bool = False,
) -> dict:
    """Run the escape hatch: check for stale/extreme positions and escape.
    
    Args:
        engines: Dict of symbol -> engine
        action_sink: The runner's action sink (for direct-live)
        event_path: Path to append escape events
        escape_config: Escape hatch config from hungry_hippo config
        dry_run: If True, report only without executing
    
    Returns:
        Dict with escape actions taken
    """
    tier1_config = escape_config.get("tier1_breakeven", {})
    tier2_config = escape_config.get("tier2_extreme", {})

    max_bars = tier1_config.get("max_bars", 20)
    max_escape_loss = tier1_config.get("max_loss", 1.0)
    cut_count = tier2_config.get("cut_count", 1)
    max_cut_loss = tier2_config.get("max_loss_per_position", 5.0)

    results = {
        "ts_utc": utc_now_iso(),
        "action": "escape_hatch_check",
        "tier1_checked": 0,
        "tier1_escaped": 0,
        "tier2_checked": 0,
        "tier2_escaped": 0,
        "tier1_pnl": 0.0,
        "tier2_pnl": 0.0,
    }

    # Tier 1: Breakeven escape
    stale = find_stale_unprofitable_positions(engines, max_bars)
    results["tier1_checked"] = len(stale)

    for item in stale:
        pnl = item["pnl"]
        if pnl >= -max_escape_loss:
            # Close at acceptable loss (~breakeven)
            results["tier1_escaped"] += 1
            results["tier1_pnl"] += pnl
            if not dry_run:
                success = _close_position_via_sink(action_sink, item["pos"], "BREAKEVEN_ESCAPE")
                item["closed"] = success

    # Tier 2: Extreme escape
    extreme = find_extreme_negative_positions(engines, cut_count, max_cut_loss)
    results["tier2_checked"] = len(extreme)

    for item in extreme:
        pnl = item["pnl"]
        results["tier2_escaped"] += 1
        results["tier2_pnl"] += pnl
        if not dry_run:
            success = _close_position_via_sink(action_sink, item["pos"], "EXTREME_ESCAPE")
            item["closed"] = success

    # Log event if any escapes happened
    if results["tier1_escaped"] > 0 or results["tier2_escaped"] > 0:
        from pathlib import Path
        event_file = Path(event_path) if not isinstance(event_path, Path) else event_path
        with open(event_file, "a") as f:
            f.write(json.dumps(results) + "\n")

    return results
