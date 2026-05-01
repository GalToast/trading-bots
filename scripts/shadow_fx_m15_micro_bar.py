#!/usr/bin/env python3
"""FX M15 Micro Bar-Level Shadow Runner

Runs FX micro lanes using BAR-LEVEL processing (not tick-level).
This matches the backtest methodology in fx_m15_deep_opt.py.

The live tick-based fxmicro lanes are starved of data because MT5
doesn't provide historical FX ticks. This runner uses M15 bars which
ARE available historically, giving realistic forward validation.

Usage:
    python scripts/shadow_fx_m15_micro_bar.py --symbol GBPUSD --step 0.0001 --max-open 80 --poll-seconds 30
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state, SymbolState
import mt5_terminal_guard
from process_singleton import acquire_singleton


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_m15_bars(symbol: str, days: int = 90) -> list[dict]:
    """Load M15 bars for symbol."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def save_state(path: Path, state: SymbolState, cfg: dict, bars_processed: int, total_bars: int) -> None:
    def ticket_dict(t):
        if isinstance(t, dict):
            return t
        return {"direction": t.direction, "entry_price": t.entry_price,
                "opened_idx": t.opened_idx, "from_rearm": t.from_rearm}
    def token_dict(t):
        if isinstance(t, dict):
            return t
        return {"direction": t.direction, "level": t.level, "level_idx": t.level_idx,
                "armed": t.armed, "cooldown_until": t.cooldown_until}
    payload = {
        "symbol": state.symbol,
        "mode": state.mode,
        "anchor": state.anchor,
        "next_sell_level": state.next_sell_level,
        "next_buy_level": state.next_buy_level,
        "open_tickets": [ticket_dict(t) for t in state.open_tickets],
        "realized_closes": state.realized_closes,
        "realized_net_usd": state.realized_net_usd,
        "rearm_opens": state.rearm_opens,
        "rearm_tokens": [token_dict(t) for t in state.rearm_tokens],
        "max_open_total": state.max_open_total,
        "anchor_resets": state.anchor_resets,
        "last_bar_time": state.last_bar_time,
        "bars_processed": bars_processed,
        "total_bars": total_bars,
        "cfg": cfg,
        "updated_at": utc_now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="GBPUSD")
    parser.add_argument("--step", type=float, default=0.0001)
    parser.add_argument("--max-open", type=int, default=80)
    parser.add_argument("--alpha", type=float, default=1.0)
    parser.add_argument("--momentum", action="store_true", default=True)
    parser.add_argument("--no-momentum", dest="momentum", action="store_false")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--state-dir", default="reports")
    args = parser.parse_args()

    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    symbol = args.symbol
    state_path = ROOT / args.state_dir / f"shadow_fx_m15_micro_{symbol.lower()}_bar_state.json"
    event_path = ROOT / args.state_dir / f"shadow_fx_m15_micro_{symbol.lower()}_bar_events.jsonl"
    lock_path = ROOT / args.state_dir / "locks" / f"shadow_fx_m15_micro_{symbol.lower()}_bar.lock"

    cfg = {
        "step": args.step,
        "max_open_per_side": args.max_open,
        "close_alpha": args.alpha,
        "close_gap": 1,
        "momentum_gate": args.momentum,
        "rearm_cooldown_bars": 0,
        "rearm_excursion_levels": 0,
        "timeframe": "M15",
        "rearm_variant": "rearm_lvl2_exc1",
    }

    with acquire_singleton(
        lock_path,
        scope=f"shadow_fx_m15_micro_bar:{symbol}",
        metadata={"symbol": symbol, "state_path": str(state_path)},
    ) as lease:
        if not lease.acquired:
            print(
                f"{symbol} micro bar shadow already running "
                f"(pid={lease.owner_pid}, lock={lock_path.name})"
            )
            mt5.shutdown()
            return 0

        try:
            print(f"FX M15 Micro Bar Shadow — {symbol}")
            print(f"  Step: {args.step}, Max open: {args.max_open}")
            print(f"  Alpha: {args.alpha}, Momentum: {args.momentum}")
            print(f"  State: {state_path}")
            mt5.symbol_select(symbol, True)

            # Load ALL bars once (historical + current)
            all_bars = load_m15_bars(symbol, args.days)
            if not all_bars:
                print(f"No bars for {symbol}")
                return 1

            print(f"  Total bars available: {len(all_bars)}")

            # Initialize or restore state
            if state_path.exists():
                with state_path.open("r", encoding="utf-8") as f:
                    saved = json.load(f)
                state = SymbolState(
                    symbol=saved["symbol"],
                    mode=saved["mode"],
                    anchor=saved["anchor"],
                    next_sell_level=saved["next_sell_level"],
                    next_buy_level=saved["next_buy_level"],
                    open_tickets=saved.get("open_tickets", []),
                    realized_closes=saved.get("realized_closes", 0),
                    realized_net_usd=saved.get("realized_net_usd", 0.0),
                    rearm_opens=saved.get("rearm_opens", 0),
                    rearm_tokens=saved.get("rearm_tokens", []),
                    max_open_total=saved.get("max_open_total", 0),
                    anchor_resets=saved.get("anchor_resets", 0),
                    last_bar_time=saved.get("last_bar_time", 0),
                )
                bars_already = saved.get("bars_processed", 0)
                print(
                    f"  Restored: {state.realized_closes} closes, "
                    f"${state.realized_net_usd:+.2f}, {bars_already} bars processed"
                )
            else:
                state = init_symbol_state(symbol, cfg, all_bars)
                bars_already = len(all_bars)
                print(f"  Fresh start: anchor={state.anchor:.5f}, bootstrapped {bars_already} bars")
                # Save immediately so off-session polling does not lose proof on crash/restart.
                save_state(state_path, state, cfg, bars_already, len(all_bars))
                print(
                    f"  Bootstrap state saved: {state.realized_closes} closes, "
                    f"${state.realized_net_usd:+.2f}"
                )

            cycle = 0
            while True:
                cycle += 1

                fresh_bars = load_m15_bars(symbol, args.days)
                if not fresh_bars:
                    time.sleep(args.poll_seconds)
                    continue

                new_bars = [b for b in fresh_bars if int(b["time"]) > state.last_bar_time]

                if new_bars:
                    state = process_symbol(symbol, cfg, fresh_bars, state)

                    bars_processed = bars_already + len(new_bars)
                    save_state(state_path, state, cfg, bars_processed, len(fresh_bars))

                    event = {
                        "action": "bar_update",
                        "symbol": symbol,
                        "new_bars": len(new_bars),
                        "total_bars": len(fresh_bars),
                        "realized_closes": state.realized_closes,
                        "realized_net_usd": round(state.realized_net_usd, 2),
                        "open_count": len(state.open_tickets),
                        "rearm_opens": state.rearm_opens,
                        "ts_utc": utc_now_iso(),
                    }
                    with event_path.open("a", encoding="utf-8") as f:
                        json.dump(event, f)
                        f.write("\n")

                    print(
                        f"  Cycle {cycle}: +{len(new_bars)} bars -> "
                        f"{state.realized_closes} closes, ${state.realized_net_usd:+.2f}, "
                        f"{len(state.open_tickets)} open"
                    )

                    bars_already = bars_processed
                else:
                    # Heartbeat: update updated_at even when no new bars (off-session)
                    save_state(state_path, state, cfg, bars_already, len(fresh_bars))

                time.sleep(args.poll_seconds)
        finally:
            mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
