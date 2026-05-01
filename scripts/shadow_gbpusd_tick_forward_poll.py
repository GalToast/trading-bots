#!/usr/bin/env python3
"""
GBPUSD Tick-Forward Shadow — Polling Mode

Polls symbol_info_tick every 5 seconds and feeds individual ticks to the engine.
This builds our own tick stream since MT5 doesn't accumulate FX tick history.

Configuration: sell=0.5/buy=1.0, gap=1/3, alpha=0.5, max_open=40
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import mt5_terminal_guard
from tick_penetration_lattice_core import (
    engine_from_args,
    load_recent_bars,
    tick_pnl_usd,
)

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "shadow_gbpusd_tick_forward_state.json"
EVENT_PATH = ROOT / "reports" / "shadow_gbpusd_tick_forward_events.jsonl"
REPORT_PATH = ROOT / "reports" / "gbpusd_tick_forward_shadow.md"

SYMBOL = "GBPUSD"
POLL_SECONDS = 5
VOLUME = 0.01


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_event(path: Path, event: dict[str, Any]) -> None:
    event["ts_utc"] = utc_now_iso()
    with path.open("a", encoding="utf-8") as f:
        json.dump(event, f)
        f.write("\n")


def load_or_create_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"started_at": utc_now_iso(), "total_ticks": 0, "cycles": 0, "last_tick_msc": 0, "errors": [], "engine_state": None}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def write_report(engine, state: dict[str, Any]) -> None:
    realized = float(engine.state.realized_net_usd or 0.0)
    closes = int(engine.state.realized_closes or 0)
    total_ticks = state.get("total_ticks", 0)
    cycles = state.get("cycles", 0)
    started_at = state.get("started_at", "unknown")
    
    # Get current tick for floating calc
    mt5.symbol_select(SYMBOL, True)
    tick = mt5.symbol_info_tick(SYMBOL)
    current_bid = float(tick.bid) if tick else 0.0
    current_ask = float(tick.ask) if tick else 0.0
    
    floating = 0.0
    for ticket in engine.state.open_tickets or []:
        direction = str(ticket.get("direction", "") or "").upper()
        fill = float(ticket.get("fill_price", ticket.get("entry_fill_price", 0.0)) or 0.0)
        exit_price = current_bid if direction == "BUY" else current_ask
        floating += tick_pnl_usd(SYMBOL, direction, fill, exit_price, volume=VOLUME)
    
    marked = realized + floating
    
    # Runtime
    try:
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - start_dt).total_seconds() / 3600
    except:
        hours = 0
    
    lines = [
        "# GBPUSD Tick-Native Forward-Shadow Validation",
        "",
        f"- Configuration: sell_step=0.5 / buy_step=1.0, sell_gap=1 / buy_gap=3",
        f"- Close alpha: 0.5, Max open per side: 40",
        f"- Started: `{started_at}`",
        f"- Status: **RUNNING** (polling every {POLL_SECONDS}s)",
        f"- Runtime: {hours:.2f}h",
        "",
        "## Current State",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total Ticks | {total_ticks:,} |",
        f"| Poll Cycles | {cycles} |",
        f"| Current Bid/Ask | {current_bid:.5f} / {current_ask:.5f} |" if current_bid > 0 else "| Current Bid/Ask | N/A |",
        f"| Realized Net (USD) | ${realized:+.2f} |",
        f"| Realized Closes | {closes} |",
        f"| Open Positions | {len(engine.state.open_tickets or [])} |",
        f"| Floating (USD) | ${floating:+.2f} |",
        f"| Marked Net (USD) | ${marked:+.2f} |",
        f"| Avg PnL/Close (USD) | ${realized/closes:+.2f} |" if closes > 0 else "| Avg PnL/Close (USD) | N/A (no closes yet) |",
        "",
        "## Interpretation",
        "",
    ]
    
    if closes == 0:
        lines.append("- No closes yet. The forward shadow needs time to accumulate tick-native execution evidence.")
        lines.append("- Check back after 4-8 hours of live tick collection.")
    elif closes < 20:
        lines.append(f"- Early signal: {closes} closes, ${realized:+.2f} net. Too few closes for reliable inference.")
        lines.append("- Continue collecting until 50+ closes for statistical confidence.")
    else:
        lines.append(f"- Forward signal: {closes} closes, ${realized:+.2f} net, ${marked:+.2f} marked.")
        if realized > 0:
            lines.append("- Positive in forward tick-native proof. Keep shadowing until the positive economics stay intact through a larger live sample.")
        else:
            lines.append("- Negative after a real forward sample. Treat this as failed-forward economics or closure-diagnosis only, not as a promotion candidate.")
    
    lines.extend([
        "",
        "## Read",
        "",
        "- This is tick-NATIVE forward validation using live `symbol_info_tick` polling.",
        "- Each tick is processed with real spread, broker-touch fill semantics, and tick-level penetration closes.",
        "- Compare against the bar-replay 60d result ($6986 modeled-live, 35.7% retention) to assess realism gap.",
        "- Positive durable economics matter more than raw close count. Do not call this forward-proof just because closes are high while net stays negative.",
    ])
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _save_snapshot(state: dict[str, Any], engine, cycles: int, total_ticks: int) -> None:
    """Atomic state save — used after close events to prevent loss on interruption."""
    state["cycles"] = cycles
    state["total_ticks"] = total_ticks
    state["last_updated"] = utc_now_iso()
    state["engine_state"] = {
        "anchor": engine.state.anchor,
        "next_sell_level": engine.state.next_sell_level,
        "next_buy_level": engine.state.next_buy_level,
        "open_tickets": engine.state.open_tickets,
        "rearm_tokens": engine.state.rearm_tokens,
        "rearm_opens": engine.state.rearm_opens,
        "realized_net_usd": engine.state.realized_net_usd,
        "realized_closes": engine.state.realized_closes,
        "anchor_resets": engine.state.anchor_resets,
        "max_open_total": engine.state.max_open_total,
        "lattice_started_time": engine.state.lattice_started_time,
        "last_tick_time": engine.state.last_tick_time,
        "last_tick_msc": engine.state.last_tick_msc,
        "last_bar_time": engine.state.last_bar_time,
    }
    save_state(state)


def main() -> int:
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1
    
    mt5.symbol_select(SYMBOL, True)
    
    try:
        state = load_or_create_state()
        cycles = state.get("cycles", 0)
        total_ticks = state.get("total_ticks", 0)
        
        # Create engine
        engine = engine_from_args(
            symbol=SYMBOL,
            timeframe_name="M1",
            step=1.0,  # max of 0.5 and 1.0
            max_open_per_side=40,
            variant_name="rearm_lvl2_exc2",
            close_alpha=0.5,
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=3,
            step_sell=0.5,
            step_buy=1.0,
            volume=VOLUME,
        )
        
        # Restore or initialize
        saved = state.get("engine_state")
        if saved:
            engine.load_snapshot(saved)
            print(f"Restored: anchor={engine.state.anchor:.5f}, realized=${engine.state.realized_net_usd:+.2f}, closes={engine.state.realized_closes}")
        else:
            bars = load_recent_bars(SYMBOL, "M1", count=120)
            if not bars:
                print("No bars for anchor initialization")
                return 1
            engine.state.last_bar_time = int(bars[-1]["time"])
            engine.prime(float(bars[-1]["close"]), int(bars[-1]["time"]))
            print(f"Initialized anchor: {engine.state.anchor:.5f}")
        
        # Poll current tick
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick:
            tick_data = {
                "time": int(getattr(tick, "time", 0) or 0),
                "time_msc": int(getattr(tick, "time_msc", 0) or 0),
                "bid": float(getattr(tick, "bid", 0.0) or 0.0),
                "ask": float(getattr(tick, "ask", 0.0) or 0.0),
                "last": float(getattr(tick, "last", 0.0) or 0.0),
                "flags": int(getattr(tick, "flags", 0) or 0),
                "volume": int(getattr(tick, "volume", 0) or 0),
            }
            
            last_msc = state.get("last_tick_msc", 0)
            if tick_data["time_msc"] > last_msc:
                closes_before = engine.state.realized_closes
                processed = engine.process_ticks([tick_data], action_sink=None, event_path=None, emit=False)
                total_ticks += processed
                state["last_tick_msc"] = tick_data["time_msc"]

                # CRITICAL: Save state IMMEDIATELY after any close to prevent loss on interruption
                if engine.state.realized_closes > closes_before:
                    _save_snapshot(state, engine, cycles, total_ticks)
                    print(f"  💾 Close detected! State saved atomically ({engine.state.realized_closes} closes)")

                if engine.state.realized_closes > 0 or len(engine.state.open_tickets) > 0:
                    append_event(EVENT_PATH, {
                        "action": "tick_processed",
                        "bid": tick_data["bid"],
                        "ask": tick_data["ask"],
                        "processed": processed,
                        "open_count": len(engine.state.open_tickets),
                        "realized": engine.state.realized_net_usd,
                        "closes": engine.state.realized_closes,
                    })
        else:
            print("No tick data available")
        
        # Save
        cycles += 1
        state["cycles"] = cycles
        state["total_ticks"] = total_ticks
        state["last_updated"] = utc_now_iso()
        state["engine_state"] = {
            "anchor": engine.state.anchor,
            "next_sell_level": engine.state.next_sell_level,
            "next_buy_level": engine.state.next_buy_level,
            "open_tickets": engine.state.open_tickets,
            "rearm_tokens": engine.state.rearm_tokens,
            "rearm_opens": engine.state.rearm_opens,
            "realized_net_usd": engine.state.realized_net_usd,
            "realized_closes": engine.state.realized_closes,
            "anchor_resets": engine.state.anchor_resets,
            "max_open_total": engine.state.max_open_total,
            "lattice_started_time": engine.state.lattice_started_time,
            "last_tick_time": engine.state.last_tick_time,
            "last_tick_msc": engine.state.last_tick_msc,
            "last_bar_time": engine.state.last_bar_time,
        }
        
        save_state(state)
        write_report(engine, state)
        
        print(f"Cycle {cycles}: {total_ticks} ticks, ${engine.state.realized_net_usd:+.2f} realized, "
              f"{engine.state.realized_closes} closes, {len(engine.state.open_tickets)} open")
        
        return 0
    
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        return 1
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
