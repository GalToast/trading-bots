#!/usr/bin/env python3
"""Unified shadow runner — all 10 symbols in a single process.

Reads config from configs/universal_10symbol_rearm.json.
Runs both FX M1 and crypto H1 symbols in one event loop.
Writes per-symbol state files + consolidated events + scoreboard.

Usage:
    python scripts/live_penetration_lattice_unified_shadow.py \
        --config configs/universal_10symbol_rearm.json \
        --state-dir reports/ \
        --poll-seconds 5 \
        --fresh-start
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    from_rearm: bool = False  # Track if this ticket came from a rearm token


from penetration_lattice_lab_v2 import dynamic_step, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until: int = 0


@dataclass
class SymbolState:
    symbol: str
    mode: str
    anchor: float = 0.0
    next_sell_level: float = 0.0
    next_buy_level: float = 0.0
    open_tickets: list[dict] = field(default_factory=list)
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    rearm_opens: int = 0
    rearm_tokens: list[dict] = field(default_factory=list)
    max_open_total: int = 0
    anchor_resets: int = 0
    breakout_flushes: int = 0
    breakout_net_usd: float = 0.0
    last_bar_time: int = 0


def load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def get_bars(symbol: str, timeframe: str) -> list[dict]:
    tf = mt5.TIMEFRAME_M1 if timeframe == "M1" else mt5.TIMEFRAME_H1
    # Load up to 90 days of bars (2160 H1 bars or 129,600 M1 bars)
    count = 24 * 90 if timeframe == "H1" else 1440 * 90
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def init_symbol_state(sym: str, cfg: dict, bars: list[dict]) -> SymbolState:
    if not bars:
        return SymbolState(symbol=sym, mode=f"{cfg['timeframe'].lower()}_stateful_rearm")
    anchor = bars[0]["close"]
    step = cfg["step"]
    return SymbolState(
        symbol=sym,
        mode=f"{cfg['timeframe'].lower()}_stateful_rearm",
        anchor=anchor,
        next_sell_level=round(anchor + step, 5),
        next_buy_level=round(anchor - step, 5),
        last_bar_time=int(bars[0]["time"]),
    )


def process_symbol(sym: str, cfg: dict, bars: list[dict], state: SymbolState) -> SymbolState:
    if not bars:
        return state

    info = mt5.symbol_info(sym)
    if info is None:
        return state

    spread_px = spread_price(info)
    base_step = cfg["step"]
    close_alpha = cfg.get("close_alpha", 1.0)
    momentum_gate = cfg.get("momentum_gate", True)
    sell_gap = cfg.get("close_gap", 1)
    buy_gap = cfg.get("close_gap", 1)
    max_open = cfg["max_open_per_side"]
    rearm_variant = cfg.get("rearm_variant", "rearm_lvl2_exc1")
    cooldown_bars = cfg.get("rearm_cooldown_bars", 0)
    rearm_excursion_levels = cfg.get("rearm_excursion_levels", 1)  # 0 = immediate, 1 = 1x step away

    # Restore tickets from state
    tickets = [Ticket(**t) for t in state.open_tickets]
    rearm_tokens = [RearmToken(**t) for t in state.rearm_tokens]
    realized_running = state.realized_net_usd

    anchor = state.anchor
    next_sell = state.next_sell_level
    next_buy = state.next_buy_level
    max_open_total = state.max_open_total

    adapt_cfg = type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Check if this is a new bar
        if int(bar["time"]) <= state.last_bar_time and state.last_bar_time > 0:
            continue
        state.last_bar_time = int(bar["time"])

        os_main = sum(1 for t in tickets if t.direction == "SELL" and not getattr(t, 'from_rearm', False))
        ob_main = sum(1 for t in tickets if t.direction == "BUY" and not getattr(t, 'from_rearm', False))
        os_rearm = sum(1 for t in tickets if t.direction == "SELL" and getattr(t, 'from_rearm', False))
        ob_rearm = sum(1 for t in tickets if t.direction == "BUY" and getattr(t, 'from_rearm', False))

        # Main lattice entries (separate limit from rearm)
        while bar["high"] >= next_sell and os_main < max_open:
            tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx, from_rearm=False))
            os_main += 1
            cs = dynamic_step(base_step, os_main, adapt_cfg)
            next_sell = round(next_sell + cs, 5)

        while bar["low"] <= next_buy and ob_main < max_open:
            tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx, from_rearm=False))
            ob_main += 1
            cs = dynamic_step(base_step, ob_main, adapt_cfg)
            next_buy = round(next_buy - cs, 5)

        # Rearm token arming
        excursion_distance = rearm_excursion_levels * base_step
        for tok in rearm_tokens:
            if tok.armed:
                continue
            if cooldown_bars > 0 and idx < tok.cooldown_until:
                continue
            if tok.direction == "SELL":
                away = tok.level - excursion_distance
                if bar["low"] <= away:
                    tok.armed = True
            else:
                away = tok.level + excursion_distance
                if bar["high"] >= away:
                    tok.armed = True

        # Consume rearm tokens (separate max_open limit from main lattice)
        for tok in list(rearm_tokens):
            if not tok.armed:
                continue
            if tok.direction == "SELL" and os_rearm >= max_open:
                break
            if tok.direction == "BUY" and ob_rearm >= max_open:
                break
            if momentum_gate:
                if tok.direction == "SELL" and bar["close"] >= tok.level:
                    continue
                if tok.direction == "BUY" and bar["close"] <= tok.level:
                    continue
            if tok.direction == "SELL" and bar["high"] >= tok.level:
                tickets.append(Ticket(direction="SELL", entry_price=tok.level, opened_idx=idx, from_rearm=True))
                rearm_tokens.remove(tok)
                os_rearm += 1
                state.rearm_opens += 1
            elif tok.direction == "BUY" and bar["low"] <= tok.level:
                tickets.append(Ticket(direction="BUY", entry_price=tok.level, opened_idx=idx, from_rearm=True))
                rearm_tokens.remove(tok)
                ob_rearm += 1
                state.rearm_opens += 1

        # SELL closes
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > sell_gap and bar["low"] <= sl[sell_gap].entry_price:
            outer = sl[0]
            ref = sl[sell_gap].entry_price
            close_px = ref + (bar["low"] - ref) * close_alpha
            pnl = unit_pnl_usd(sym, "SELL", outer.entry_price, close_px, spread_px)
            realized_running += pnl
            tickets.remove(outer)
            state.realized_closes += 1
            # Create rearm token ONLY from main lattice closes (not rearm-origin tickets)
            if not getattr(outer, 'from_rearm', False):
                level_idx = int(round((outer.entry_price - anchor) / base_step))
                if level_idx >= 2:
                    cd = idx + cooldown_bars if cooldown_bars > 0 else 0
                    # Create a pre-armed rearm token (approximates "churn" from honing engines)
                    # This allows immediate re-entry without excursion requirement
                    rearm_tokens.append(RearmToken(
                        direction="SELL", level=outer.entry_price, level_idx=level_idx,
                        armed=True, cooldown_until=cd,  # Pre-armed = immediate re-entry
                    ))
            sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)

        # BUY closes
        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > buy_gap and bar["high"] >= bl[buy_gap].entry_price:
            outer = bl[0]
            ref = bl[buy_gap].entry_price
            close_px = ref + (bar["high"] - ref) * close_alpha
            pnl = unit_pnl_usd(sym, "BUY", outer.entry_price, close_px, spread_px)
            realized_running += pnl
            tickets.remove(outer)
            state.realized_closes += 1
            # Create rearm token ONLY from main lattice closes (not rearm-origin tickets)
            if not getattr(outer, 'from_rearm', False):
                level_idx = int(round((anchor - outer.entry_price) / base_step))
                if level_idx >= 2:
                    cd = idx + cooldown_bars if cooldown_bars > 0 else 0
                    # Create a pre-armed rearm token (approximates "churn" from honing engines)
                    rearm_tokens.append(RearmToken(
                        direction="BUY", level=outer.entry_price, level_idx=level_idx,
                        armed=True, cooldown_until=cd,
                    ))
            bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)

        # Anchor reset
        if not tickets and abs(bar["close"] - anchor) >= base_step:
            anchor = bar["close"]
            next_sell = round(anchor + base_step, 5)
            next_buy = round(anchor - base_step, 5)
            state.anchor_resets += 1
            rearm_tokens = []

        max_open_total = max(max_open_total, len(tickets))

    # Update state
    state.anchor = anchor
    state.next_sell_level = next_sell
    state.next_buy_level = next_buy
    state.open_tickets = [asdict(t) for t in tickets]
    state.rearm_tokens = [asdict(t) for t in rearm_tokens]
    state.realized_net_usd = round(realized_running, 3)
    state.max_open_total = max_open_total

    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "configs" / "universal_10symbol_rearm.json"))
    parser.add_argument("--state-dir", default=str(ROOT / "reports"))
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()

    mt5.initialize()
    config = load_config(args.config)
    symbols = config["symbols"]
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Initialize states
    states: dict[str, SymbolState] = {}
    for sym, cfg in symbols.items():
        state_path = state_dir / f"unified_shadow_{sym.lower()}_state.json"
        if not args.fresh_start and state_path.exists():
            with open(state_path) as f:
                data = json.load(f)
            states[sym] = SymbolState(**data.get("symbols", {}).get(sym, {}))
        else:
            bars = get_bars(sym, cfg["timeframe"])
            states[sym] = init_symbol_state(sym, cfg, bars)

    print(f"\nUnified Shadow Runner — {len(symbols)} symbols")
    print(f"Poll interval: {args.poll_seconds}s, Fresh start: {args.fresh_start}")
    print("-" * 70)

    iteration = 0
    try:
        while True:
            iteration += 1
            start = time.time()

            for sym, cfg in symbols.items():
                bars = get_bars(sym, cfg["timeframe"])
                if not bars:
                    continue
                state = process_symbol(sym, cfg, bars, states[sym])
                states[sym] = state

                # Write per-symbol state
                state_path = state_dir / f"unified_shadow_{sym.lower()}_state.json"
                with open(state_path, "w") as f:
                    json.dump({"symbols": {sym: asdict(state)}}, f, indent=2)

            # Print scoreboard
            if iteration % 12 == 0:  # Every ~60 seconds
                print(f"\n{'Symbol':<12} {'Mode':<15} {'Open':>5} {'Closes':>7} {'Realized $':>11} {'Rearm':>6}")
                print("-" * 70)
                for sym, cfg in symbols.items():
                    s = states[sym]
                    print(f"{sym:<12} {s.mode:<15} {len(s.open_tickets):>5} {s.realized_closes:>7} ${s.realized_net_usd:>10,.2f} {s.rearm_opens:>6}")

            elapsed = time.time() - start
            if elapsed < args.poll_seconds:
                time.sleep(args.poll_seconds - elapsed)

    except KeyboardInterrupt:
        print("\nShutting down unified shadow runner...")
        # Final state save
        for sym in symbols:
            state_path = state_dir / f"unified_shadow_{sym.lower()}_state.json"
            with open(state_path, "w") as f:
                json.dump({"symbols": {sym: asdict(states[sym])}}, f, indent=2)
        mt5.shutdown()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
