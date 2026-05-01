#!/usr/bin/env python3
"""
COUNTER-TREND REARM — COMPREHENSIVE SWEEP

HYPOTHESIS:
  After cascade closes SELLs (price reversed down), immediately arming BUY tokens
  captures the reversal momentum. Same for BUY→SELL.

  Current: cascade closes → same-direction rearm → tokens never arm → 5 opens/462 cycles
  Counter: cascade closes → OPPOSITE-direction rearm → tokens arm as reversal continues

MECHANICS:
  - SELL cascade close (price went down) → arm BUY token at anchor - step
  - BUY cascade close (price went up) → arm SELL token at anchor + step
  - No excursion distance (immediate arming)
  - Optional cooldown to prevent immediate re-close churn

This tests in the bar-level shadow engine for fast iteration.
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
from dataclasses import dataclass, field, asdict
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import dynamic_step, pip_size_for, spread_price, unit_pnl_usd

mt5.initialize()

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    from_rearm: bool = False

@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until: int = 0
    is_counter: bool = False  # True if this is a counter-trend rearm

@dataclass
class SymbolState:
    symbol: str
    mode: str = "tick"
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
    last_bar_time: int = 0
    # Counter-rearm specific
    counter_rearm_opens: int = 0
    counter_rearm_closes: int = 0
    same_direction_rearm_opens: int = 0
    same_direction_rearm_closes: int = 0


def init_symbol_state(sym: str, cfg: dict, bars: list) -> SymbolState:
    anchor = bars[0]["close"]
    base_step = cfg["step"]
    return SymbolState(
        symbol=sym, anchor=anchor,
        next_sell_level=round(anchor + base_step, 5),
        next_buy_level=round(anchor - base_step, 5),
        last_bar_time=int(bars[0]["time"]),
    )


def process_symbol(sym: str, cfg: dict, bars: list, state: SymbolState) -> SymbolState:
    """Bar-level simulation with counter-trend rearm support."""
    if not bars:
        return state

    info = mt5.symbol_info(sym)
    if info is None:
        return state

    spread_px = spread_price(info)
    base_step = cfg["step"]
    close_alpha = cfg.get("close_alpha", 1.0)
    momentum_gate = cfg.get("momentum_gate", False)
    sell_gap = cfg.get("close_gap", 1)
    buy_gap = cfg.get("close_gap", 1)
    max_open = cfg["max_open_per_side"]

    # Counter-rearm config
    counter_rearm_enabled = cfg.get("counter_rearm", False)
    counter_rearm_excursion = cfg.get("counter_rearm_excursion", 0.0)  # steps
    counter_rearm_cooldown = cfg.get("counter_rearm_cooldown", 0)  # bars
    counter_rearm_levels = cfg.get("counter_rearm_levels", 1)  # how many levels deep to arm

    # Same-direction rearm config
    same_excursion = cfg.get("same_excursion", 1.0)  # steps
    same_cooldown = cfg.get("same_cooldown", 0)  # bars
    same_min_level = cfg.get("same_min_level", 2)  # min level_idx for same-direction rearm

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
        if int(bar["time"]) <= state.last_bar_time and state.last_bar_time > 0:
            continue
        state.last_bar_time = int(bar["time"])

        os_main = sum(1 for t in tickets if t.direction == "SELL" and not t.from_rearm)
        ob_main = sum(1 for t in tickets if t.direction == "BUY" and not t.from_rearm)
        os_rearm = sum(1 for t in tickets if t.direction == "SELL" and t.from_rearm)
        ob_rearm = sum(1 for t in tickets if t.direction == "BUY" and t.from_rearm)

        # Main lattice opens
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

        # Arm existing rearm tokens
        excursion_distance = same_excursion * base_step
        for tok in rearm_tokens:
            if tok.armed or tok.cooldown_until > idx:
                continue
            if tok.direction == "SELL":
                if bar["low"] <= tok.level - excursion_distance:
                    tok.armed = True
            else:
                if bar["high"] >= tok.level + excursion_distance:
                    tok.armed = True

        # Arm counter-trend rearm tokens (excursion in OPPOSITE direction from entry level)
        counter_excursion = counter_rearm_excursion * base_step
        for tok in rearm_tokens:
            if not tok.is_counter or tok.armed or tok.cooldown_until > idx:
                continue
            if tok.direction == "SELL":
                # Counter-SELL arms when price goes UP past level (original reversal direction continues)
                if bar["high"] >= tok.level + counter_excursion:
                    tok.armed = True
            else:
                # Counter-BUY arms when price goes DOWN past level
                if bar["low"] <= tok.level - counter_excursion:
                    tok.armed = True

        # Consume armed rearm tokens
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
                if tok.is_counter:
                    state.counter_rearm_opens += 1
                else:
                    state.same_direction_rearm_opens += 1
            elif tok.direction == "BUY" and bar["low"] <= tok.level:
                tickets.append(Ticket(direction="BUY", entry_price=tok.level, opened_idx=idx, from_rearm=True))
                rearm_tokens.remove(tok)
                ob_rearm += 1
                state.rearm_opens += 1
                if tok.is_counter:
                    state.counter_rearm_opens += 1
                else:
                    state.same_direction_rearm_opens += 1

        # === SELL CASCADE CLOSES ===
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > sell_gap and bar["low"] <= sl[sell_gap].entry_price:
            outer = sl[0]
            ref = sl[sell_gap].entry_price
            close_px = ref + (bar["low"] - ref) * close_alpha
            pnl = unit_pnl_usd(sym, "SELL", outer.entry_price, close_px, spread_px)
            realized_running += pnl
            tickets.remove(outer)
            state.realized_closes += 1
            level_idx = int(round((outer.entry_price - anchor) / base_step))

            # Create same-direction rearm (existing behavior)
            if not outer.from_rearm and level_idx >= same_min_level:
                cd = idx + same_cooldown if same_cooldown > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="SELL", level=outer.entry_price, level_idx=level_idx,
                    armed=False, cooldown_until=cd, is_counter=False,
                ))

            # === COUNTER-TREND REARM ===
            if counter_rearm_enabled and not outer.from_rearm:
                # SELL closed → price reversed DOWN → arm BUY tokens in counter direction
                for lvl in range(1, counter_rearm_levels + 1):
                    counter_level = anchor - lvl * base_step
                    cd = idx + counter_rearm_cooldown if counter_rearm_cooldown > 0 else 0
                    rearm_tokens.append(RearmToken(
                        direction="BUY", level=counter_level, level_idx=lvl,
                        armed=False, cooldown_until=cd, is_counter=True,
                    ))

            sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)

        # === BUY CASCADE CLOSES ===
        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > buy_gap and bar["high"] >= bl[buy_gap].entry_price:
            outer = bl[0]
            ref = bl[buy_gap].entry_price
            close_px = ref + (bar["high"] - ref) * close_alpha
            pnl = unit_pnl_usd(sym, "BUY", outer.entry_price, close_px, spread_px)
            realized_running += pnl
            tickets.remove(outer)
            state.realized_closes += 1
            level_idx = int(round((anchor - outer.entry_price) / base_step))

            # Create same-direction rearm (existing behavior)
            if not outer.from_rearm and level_idx >= same_min_level:
                cd = idx + same_cooldown if same_cooldown > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="BUY", level=outer.entry_price, level_idx=level_idx,
                    armed=False, cooldown_until=cd, is_counter=False,
                ))

            # === COUNTER-TREND REARM ===
            if counter_rearm_enabled and not outer.from_rearm:
                # BUY closed → price reversed UP → arm SELL tokens in counter direction
                for lvl in range(1, counter_rearm_levels + 1):
                    counter_level = anchor + lvl * base_step
                    cd = idx + counter_rearm_cooldown if counter_rearm_cooldown > 0 else 0
                    rearm_tokens.append(RearmToken(
                        direction="SELL", level=counter_level, level_idx=lvl,
                        armed=False, cooldown_until=cd, is_counter=True,
                    ))

            bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)

        # Anchor reset
        if not tickets and abs(bar["close"] - anchor) >= base_step:
            anchor = bar["close"]
            next_sell = round(anchor + base_step, 5)
            next_buy = round(anchor - base_step, 5)
            state.anchor_resets += 1
            rearm_tokens = []  # Clear all tokens on reset

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


def main():
    symbols_cfg = {
        "EURUSD": {"tf": mt5.TIMEFRAME_M15, "step": 0.00015, "days": 30},
        "GBPUSD": {"tf": mt5.TIMEFRAME_M15, "step": 0.00015, "days": 30},
    }

    for sym_name, sym_cfg in symbols_cfg.items():
        print(f"\n{'='*110}")
        print(f"=== {sym_name} M15 — {sym_cfg['days']} DAYS ===")
        print(f"{'='*110}")

        bars_raw = mt5.copy_rates_from_pos(sym_name, sym_cfg["tf"], 0, 24 * 4 * sym_cfg["days"])
        bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars_raw]
        total_hrs = len(bars) * 15 / 60
        print(f"Loaded {len(bars)} bars ({total_hrs:.0f} hours)")
        print()

        # Matrix: baseline vs counter-rearm sweeps
        configs = [
            {"label": "BASELINE: no counter-rearm", "counter": False},
            {"label": "COUNTER-1: 1 level, exc=0, cd=0", "counter": True, "levels": 1, "exc": 0.0, "cd": 0},
        ]

        step_values = [sym_cfg["step"]]
        max_open_values = [40]

        results = []
        for cfg in configs:
            for step in step_values:
                for mo in max_open_values:
                    full_label = f"{cfg['label']} step={step} mo={mo}"
                    c = {
                        "step": step,
                        "max_open_per_side": mo,
                        "close_alpha": 1.0,
                        "close_gap": 0,  # cascade
                        "momentum_gate": False,
                        "counter_rearm": cfg.get("counter", False),
                        "counter_rearm_excursion": cfg.get("exc", 0.0),
                        "counter_rearm_cooldown": cfg.get("cd", 0),
                        "counter_rearm_levels": cfg.get("levels", 1),
                        "same_excursion": 1.0,
                        "same_cooldown": 0,
                        "same_min_level": 2,
                    }
                    state = init_symbol_state(sym_name, c, bars)
                    state = process_symbol(sym_name, c, bars, state)
                    closes = state.realized_closes
                    net = state.realized_net_usd
                    avg = net / closes if closes > 0 else 0
                    per_hr = net / total_hrs
                    results.append((full_label, {
                        "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
                        "resets": state.anchor_resets,
                        "rearm_opens": state.rearm_opens,
                        "counter_opens": state.counter_rearm_opens,
                        "counter_closes": state.counter_rearm_closes,
                        "same_opens": state.same_direction_rearm_opens,
                        "same_closes": state.same_direction_rearm_closes,
                        "max_open": state.max_open_total,
                    }))

        # Sort by $/hr
        results.sort(key=lambda x: x[1]["per_hr"], reverse=True)

        print(f"{'Config':<65} {'$/hr':>8} {'Closes':>7} {'$/close':>8} {'Rearm':>6} {'Counter':>8} {'Resets':>7}")
        print("-" * 110)
        for label, r in results[:20]:
            print(f"{label:<65} ${r['per_hr']:>7.2f} {r['closes']:>7} ${r['avg']:>7.2f} {r['rearm_opens']:>6} {r['counter_opens']:>8} {r['resets']:>7}")
        print("=" * 110)

        # Find best improvement
        baseline = [r for l, r in results if "BASELINE" in l]
        counter = [r for l, r in results if "COUNTER" in l]
        if baseline and counter:
            best_baseline = max(baseline, key=lambda x: x["per_hr"])
            best_counter = max(counter, key=lambda x: x["per_hr"])
            improvement = (best_counter["per_hr"] - best_baseline["per_hr"]) / max(abs(best_baseline["per_hr"]), 0.01)
            print(f"\nBest baseline: ${best_baseline['per_hr']:.2f}/hr")
            print(f"Best counter:  ${best_counter['per_hr']:.2f}/hr")
            print(f"Improvement:   {improvement:+.1%}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
