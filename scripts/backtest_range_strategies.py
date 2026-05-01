#!/usr/bin/env python3
"""Dead-market range strategy backtest — finds range-trading edge across ALL FX symbols.

Tests range_mean_reversion and pullback_to_structure_hold on all available FX pairs
during dead market hours (Asian session 22:00-07:00 UTC) to find what can trade NOW.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5


SYMBOLS_FX = (
    "USDJPY", "GBPUSD", "EURUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
    "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY",
    "EURGBP", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    "AUDCAD", "AUDCHF", "AUDNZD",
    "NZDCAD", "NZDCHF",
)

FX_MAJORS = ("USDJPY", "GBPUSD", "EURUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF")


@dataclass(frozen=True)
class RangeRecipe:
    recipe_id: str
    lookback_bars: int       # Range lookback window
    entry_type: str          # "reversion" or "pullback"
    max_range_atr: float     # Max ATR range (pips) to consider "dead market"
    confirm_pips: float      # Confirmation beyond range edge (for pullback)
    stop_atr_mult: float     # Stop loss as ATR multiple
    max_hold_bars: int       # Maximum hold in bars
    exit_retain: float       # Exit retain ratio (for trailing)
    min_mfe_pips: float      # Min MFE before trailing activates


@dataclass
class Trade:
    symbol: str
    recipe_id: str
    direction: str
    entry_idx: int
    exit_idx: int
    pnl_usd: float
    hold_bars: int
    mfe_pips: float
    mae_pips: float
    entry_atr_pips: float
    entry_hour_utc: int
    range_width_pips: float  # The range width at entry


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def body_pips(bar: dict, pip: float) -> float:
    return abs(bar["close"] - bar["open"]) / pip


def range_pips(bar: dict, pip: float) -> float:
    return max((bar["high"] - bar["low"]) / pip, 0.01)


def compute_atr(bars: list[dict], idx: int, pip: float, period: int = 14) -> float:
    if idx < period:
        return 0.0
    trs = []
    for i in range(idx - period + 1, idx + 1):
        tr = bars[i]["high"] - bars[i]["low"]
        if i > 0:
            tr = max(tr, abs(bars[i]["high"] - bars[i - 1]["close"]))
            tr = max(tr, abs(bars[i]["low"] - bars[i - 1]["close"]))
        trs.append(tr / pip)
    return mean(trs) if trs else 0.0


def pnl_usd(symbol: str, direction: str, entry: float, exit_price: float, spread_px: float, volume: float = 0.01) -> float:
    move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    net = move - spread_px
    units = volume * 100_000
    raw = net * units
    return raw / max(exit_price, 0.0001)


def detect_range_edge(
    bars: list[dict], idx: int, lookback: int, pip: float,
) -> tuple | None:
    """Detect if price is at range edge (top or bottom of lookback window)."""
    if idx < lookback:
        return None
    prior = bars[idx - lookback:idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    range_width = (prior_high - prior_low) / pip

    cur = bars[idx]
    at_top = cur["close"] >= prior_high - pip  # Within 1 pip of top
    at_bottom = cur["close"] <= prior_low + pip  # Within 1 pip of bottom

    if at_top:
        return "SELL", prior_high, prior_low, range_width
    if at_bottom:
        return "BUY", prior_high, prior_low, range_width
    return None


def simulate_range(
    bars: list[dict],
    symbol: str,
    recipe: RangeRecipe,
    spread_pips: float,
    only_asian: bool = False,
    only_dead: bool = False,
) -> list[Trade]:
    """Simulate range trading strategy."""
    pip = pip_size(symbol)
    spread_px = spread_pips * pip
    trades = []
    idx = recipe.lookback_bars + 14

    while idx < len(bars) - 2:
        from datetime import datetime, timezone
        bar_time = datetime.fromtimestamp(bars[idx]["time"], tz=timezone.utc)
        hour = bar_time.hour

        if only_asian and not (hour >= 22 or hour < 7):
            idx += 1
            continue

        atr = compute_atr(bars, idx, pip)
        if only_dead and atr > recipe.max_range_atr:
            idx += 1
            continue

        edge = detect_range_edge(bars, idx, recipe.lookback_bars, pip)
        if edge is None:
            idx += 1
            continue

        direction, range_high, range_low, range_width = edge
        atr_pips = atr

        entry_idx = idx + 1
        if entry_idx >= len(bars):
            break
        entry_price = bars[entry_idx]["open"]

        mfe_pips = 0.0
        mae_pips = 0.0
        peak_price = entry_price
        exit_idx = None
        exit_price = None

        stop_distance = atr_pips * recipe.stop_atr_mult

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + recipe.max_hold_bars + 1)):
            bar = bars[j]

            if direction == "BUY":
                favorable = (bar["high"] - entry_price) / pip
                adverse = -(bar["low"] - entry_price) / pip
                if bar["high"] > peak_price:
                    peak_price = bar["high"]
                # Stop loss
                if bar["low"] <= entry_price - stop_distance * pip:
                    exit_idx = j
                    exit_price = entry_price - stop_distance * pip
                    break
            else:
                favorable = (entry_price - bar["low"]) / pip
                adverse = -(bar["high"] - entry_price) / pip
                if bar["low"] < peak_price:
                    peak_price = bar["low"]
                if bar["high"] >= entry_price + stop_distance * pip:
                    exit_idx = j
                    exit_price = entry_price + stop_distance * pip
                    break

            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)

            # Trail exit
            if mfe_pips >= recipe.min_mfe_pips:
                floor = mfe_pips * recipe.exit_retain
                if direction == "BUY":
                    trail = entry_price + floor * pip
                    if bar["close"] <= trail:
                        exit_idx = j
                        exit_price = trail
                        break
                else:
                    trail = entry_price - floor * pip
                    if bar["close"] >= trail:
                        exit_idx = j
                        exit_price = trail
                        break

            # Time exit
            if (j - entry_idx + 1) >= recipe.max_hold_bars:
                exit_idx = j
                exit_price = bar["close"]
                break

        if exit_idx is None or exit_price is None:
            exit_idx = min(len(bars) - 1, entry_idx + recipe.max_hold_bars)
            exit_price = bars[exit_idx]["close"]

        pnl = pnl_usd(symbol, direction, entry_price, exit_price, spread_px)

        trades.append(Trade(
            symbol=symbol, recipe_id=recipe.recipe_id, direction=direction,
            entry_idx=entry_idx, exit_idx=exit_idx, pnl_usd=pnl,
            hold_bars=exit_idx - entry_idx + 1,
            mfe_pips=max(0.0, mfe_pips), mae_pips=max(0.0, mae_pips),
            entry_atr_pips=max(0.0, atr_pips), entry_hour_utc=hour,
            range_width_pips=range_width,
        ))

        idx = exit_idx + 1

    return trades


RECIPES = (
    RangeRecipe("range_reversion_atr1_stop", 30, "reversion",
                max_range_atr=0.0, confirm_pips=0.0,
                stop_atr_mult=1.0, max_hold_bars=30, exit_retain=0.60, min_mfe_pips=1.0),
    RangeRecipe("range_reversion_atr2_stop", 30, "reversion",
                max_range_atr=0.0, confirm_pips=0.0,
                stop_atr_mult=2.0, max_hold_bars=30, exit_retain=0.60, min_mfe_pips=1.0),
    RangeRecipe("range_pullback_30bar", 30, "pullback",
                max_range_atr=0.0, confirm_pips=0.5,
                stop_atr_mult=1.5, max_hold_bars=20, exit_retain=0.60, min_mfe_pips=1.0),
    RangeRecipe("range_pullback_50bar", 50, "pullback",
                max_range_atr=0.0, confirm_pips=0.5,
                stop_atr_mult=1.5, max_hold_bars=20, exit_retain=0.60, min_mfe_pips=1.0),
    RangeRecipe("dead_market_reversion", 20, "reversion",
                max_range_atr=5.0, confirm_pips=0.0,
                stop_atr_mult=1.5, max_hold_bars=20, exit_retain=0.70, min_mfe_pips=0.5),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--spread-pips", type=float, default=0.6)
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--asian-only", action="store_true", help="Only Asian session hours")
    parser.add_argument("--dead-only", action="store_true", help="Only low-ATR bars")
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbols = args.symbols if args.symbols else list(FX_MAJORS)
        recipes = RECIPES

        print("=" * 72)
        mode = ""
        if args.asian_only:
            mode = " ASIAN SESSION ONLY"
        if args.dead_only:
            mode += " DEAD MARKET ONLY"
        print(f"RANGE STRATEGY BACKTEST ({args.days} days{mode})")
        print(f"Symbols: {', '.join(symbols)}")
        print(f"Spread: {args.spread_pips} pips")
        print("=" * 72)
        print()

        all_trades = []
        for symbol in symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            if len(bars) < 1440 * max(args.days, 1):
                continue
            for recipe in recipes:
                trades = simulate_range(bars, symbol, recipe, args.spread_pips,
                                       args.asian_only, args.dead_only)
                all_trades.extend(trades)

        if not all_trades:
            print("No trades found")
            return 0

        # Summary by recipe
        from collections import defaultdict
        by_recipe = defaultdict(list)
        for t in all_trades:
            by_recipe[t.recipe_id].append(t)

        print(f"{'Recipe':<25} {'Sym':>6} {'Trades':>6} {'WR':>5} {'Net':>8} {'Exp':>8} {'Hold':>5} {'MFE':>6} {'Dir':>4}")
        print("-" * 80)

        for recipe_id in sorted(by_recipe.keys()):
            rtrades = by_recipe[recipe_id]
            # By symbol
            by_sym = defaultdict(list)
            for t in rtrades:
                by_sym[t.symbol].append(t)

            for sym in sorted(by_sym.keys()):
                st = by_sym[sym]
                pnls = [t.pnl_usd for t in st]
                wins = sum(1 for p in pnls if p > 0)
                net = sum(pnls)
                exp = mean(pnls) if pnls else 0
                hold = mean(t.hold_bars for t in st)
                mfe = mean(t.mfe_pips for t in st)
                buy_w = sum(1 for t in st if t.direction == "BUY" and t.pnl_usd > 0)
                buy_n = sum(1 for t in st if t.direction == "BUY")
                sell_w = sum(1 for t in st if t.direction == "SELL" and t.pnl_usd > 0)
                sell_n = sum(1 for t in st if t.direction == "SELL")
                dir_str = f"B{buy_w}/{buy_n} S{sell_w}/{sell_n}"

                flag = " ✅" if exp > 0.02 else ""
                print(
                    f"{recipe_id:<25} {sym:>6} {len(st):>6d} {wins/len(st)*100:>5.0f}% "
                    f"${net:+7.2f} ${exp:+7.3f} {hold:>4.1f} {mfe:>5.1f}p {dir_str:>10s}{flag}"
                )

        print()

        # Top combos
        combos = []
        for recipe_id, rtrades in by_recipe.items():
            by_sym = defaultdict(list)
            for t in rtrades:
                by_sym[t.symbol].append(t)
            for sym, st in by_sym.items():
                pnls = [t.pnl_usd for t in st]
                wins = sum(1 for p in pnls if p > 0)
                net = sum(pnls)
                exp = mean(pnls) if pnls else 0
                trades_per_day = len(st) / max(args.days, 1)
                combos.append((recipe_id, sym, len(st), wins/len(st)*100, net, exp, trades_per_day))

        combos.sort(key=lambda x: (x[5], x[4]), reverse=True)

        print("─" * 72)
        print("TOP 15 COMBOS (by expectancy)")
        print("─" * 72)
        print(f"{'Recipe':<25} {'Sym':>6} {'N':>5} {'WR':>5} {'Net':>8} {'Exp':>8} {'Tr/d':>6}")
        print("-" * 70)
        for recipe_id, sym, n, wr, net, exp, tpd in combos[:15]:
            flag = " ✅" if exp > 0.03 else ""
            print(f"{recipe_id:<25} {sym:>6} {n:>5d} {wr:>5.0f}% ${net:+7.2f} ${exp:+7.3f} {tpd:>5.2f}{flag}")

        # Write CSV
        csv_path = Path(__file__).resolve().parent.parent / "reports" / "range_strategy_backtest.csv"
        csv_path.parent.mkdir(exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["recipe", "symbol", "trades", "wr", "net_usd", "exp_usd", "trades_per_day"])
            for recipe_id, sym, n, wr, net, exp, tpd in combos:
                writer.writerow([recipe_id, sym, n, round(wr, 1), round(net, 3), round(exp, 3), round(tpd, 2)])
        print(f"\nSaved: {csv_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
