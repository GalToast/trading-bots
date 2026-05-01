#!/usr/bin/env python3
from __future__ import annotations

import csv
from dataclasses import dataclass

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import ROOT, load_bars, pip_size_for, spread_price


VOLUME = 0.01
DAYS = 60
SYMBOL_STEPS = {
    "GBPUSD": 2.0,
    "EURUSD": 2.5,
    "NZDUSD": 1.5,
}
ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]


@dataclass
class Pos:
    direction: str
    entry: float


def pnl_usd(symbol: str, direction: str, entry: float, exit_px: float, spread_px: float, contract_size: float) -> float:
    if not symbol.endswith("USD") or symbol.startswith("USD"):
        raise ValueError(f"Fast PnL helper only supports USD-quoted majors in this sweep: {symbol}")
    direction_sign = 1.0 if direction == "BUY" else -1.0
    gross = (exit_px - entry) * direction_sign * contract_size * VOLUME
    spread_cost = spread_px * contract_size * VOLUME
    return gross - spread_cost


def adaptive_step(base_step: float, count: int) -> float:
    if count >= 20:
        return base_step * 2.0
    if count >= 10:
        return base_step * 1.5
    return base_step


def lerp_exit(level: float, extreme: float, alpha: float) -> float:
    return level + (extreme - level) * alpha


def run_lattice(symbol: str, bars: list[dict], info, step_pips: float, close_mode: str, alpha: float) -> dict:
    if not bars:
        return {}

    pip = pip_size_for(info)
    spread = spread_price(info)
    base_step = step_pips * pip
    contract_size = float(info.trade_contract_size or 100000.0)

    anchor = bars[0]["close"]
    sell_level = anchor + base_step
    buy_level = anchor - base_step

    positions: list[Pos] = []
    realized: list[float] = []
    worst_seen = 0.0
    max_open = 0

    for bar in bars[1:]:
        sell_count = sum(1 for p in positions if p.direction == "SELL")
        buy_count = sum(1 for p in positions if p.direction == "BUY")

        while bar["high"] >= sell_level and sell_count < 20:
            positions.append(Pos("SELL", sell_level))
            sell_count += 1
            sell_level += adaptive_step(base_step, sell_count)

        while bar["low"] <= buy_level and buy_count < 20:
            positions.append(Pos("BUY", buy_level))
            buy_count += 1
            buy_level -= adaptive_step(base_step, buy_count)

        if close_mode == "two_level":
            sells = sorted((p for p in positions if p.direction == "SELL"), key=lambda p: p.entry, reverse=True)
            while len(sells) >= 2 and bar["low"] <= sells[1].entry:
                close_px = lerp_exit(sells[1].entry, bar["low"], alpha)
                pnl = pnl_usd(symbol, "SELL", sells[0].entry, close_px, spread, contract_size)
                if pnl <= 0:
                    break
                realized.append(pnl)
                positions.remove(sells[0])
                sells = sorted((p for p in positions if p.direction == "SELL"), key=lambda p: p.entry, reverse=True)

            buys = sorted((p for p in positions if p.direction == "BUY"), key=lambda p: p.entry)
            while len(buys) >= 2 and bar["high"] >= buys[1].entry:
                close_px = lerp_exit(buys[1].entry, bar["high"], alpha)
                pnl = pnl_usd(symbol, "BUY", buys[0].entry, close_px, spread, contract_size)
                if pnl <= 0:
                    break
                realized.append(pnl)
                positions.remove(buys[0])
                buys = sorted((p for p in positions if p.direction == "BUY"), key=lambda p: p.entry)

        elif close_mode == "all_profitable":
            sells = sorted((p for p in positions if p.direction == "SELL"), key=lambda p: p.entry, reverse=True)
            if len(sells) >= 2 and bar["low"] <= sells[1].entry:
                close_px = lerp_exit(sells[1].entry, bar["low"], alpha)
                profitable = [p for p in sells if pnl_usd(symbol, "SELL", p.entry, close_px, spread, contract_size) > 0]
                for p in profitable:
                    realized.append(pnl_usd(symbol, "SELL", p.entry, close_px, spread, contract_size))
                    positions.remove(p)

            buys = sorted((p for p in positions if p.direction == "BUY"), key=lambda p: p.entry)
            if len(buys) >= 2 and bar["high"] >= buys[1].entry:
                close_px = lerp_exit(buys[1].entry, bar["high"], alpha)
                profitable = [p for p in buys if pnl_usd(symbol, "BUY", p.entry, close_px, spread, contract_size) > 0]
                for p in profitable:
                    realized.append(pnl_usd(symbol, "BUY", p.entry, close_px, spread, contract_size))
                    positions.remove(p)

        max_open = max(max_open, len(positions))
        floating_now = [pnl_usd(symbol, p.direction, p.entry, bar["close"], spread, contract_size) for p in positions]
        if floating_now:
            worst_seen = max(worst_seen, abs(min(floating_now)))

    final_close = bars[-1]["close"]
    floating = [pnl_usd(symbol, p.direction, p.entry, final_close, spread, contract_size) for p in positions]
    realized_net = sum(realized)
    floating_net = sum(floating)
    return {
        "combined": round(realized_net + floating_net, 2),
        "realized": round(realized_net, 2),
        "floating": round(floating_net, 2),
        "closes": len(realized),
        "max_open": max_open,
        "worst_seen": round(worst_seen, 2),
    }


def main() -> int:
    if not mt5.initialize():
        print("MT5 init failed")
        return 1

    rows: list[dict] = []
    for symbol, step in SYMBOL_STEPS.items():
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, DAYS)
        if not bars or info is None:
            continue
        for close_mode in ["two_level", "all_profitable"]:
            for alpha in ALPHAS:
                result = run_lattice(symbol, bars, info, step, close_mode, alpha)
                rows.append({
                    "symbol": symbol,
                    "days": DAYS,
                    "step": step,
                    "close_mode": close_mode,
                    "alpha": alpha,
                    "close_ref_label": f"{int(alpha * 100)}pct_to_extreme",
                    **result,
                    "daily": round(result["combined"] / DAYS, 3),
                })

    output = ROOT / "reports" / "apex_close_realism_ladder.csv"
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {output}")
    for close_mode in ["two_level", "all_profitable"]:
        subset = [r for r in rows if r["close_mode"] == close_mode]
        print(f"\n{close_mode}")
        for alpha in ALPHAS:
            picks = [r for r in subset if r["alpha"] == alpha]
            total = sum(r["combined"] for r in picks)
            daily = total / DAYS
            print(f"  alpha={alpha:>4.2f} total={total:+9.2f} daily={daily:+7.2f}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
