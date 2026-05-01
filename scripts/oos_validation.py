#!/usr/bin/env python3
"""
30d Out-of-Sample Validation — testing ultra-tight apex configs on fresh data.

Tests the ultra-tight configs (GBPUSD 0.10/50, EURUSD 0.20/50, NZDUSD 0.15/50)
on days 61-90 (the 30d sample AFTER the 60d training window) to check for overfitting.
"""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import (
    ROOT,
    load_bars,
    pip_size_for,
    spread_price,
)


VOLUME = 0.01
FILL_CAP_PIPS = 10.0


def pnl_usd(symbol, direction, entry, exit_px, spread_px, vol=VOLUME):
    ot = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(ot, symbol, vol, entry, exit_px)
    if gross is None:
        return 0.0
    if direction == "BUY":
        sc = mt5.order_calc_profit(ot, symbol, vol, entry + spread_px, entry)
    else:
        sc = mt5.order_calc_profit(ot, symbol, vol, entry, entry + spread_px)
    return float(gross) - abs(float(sc or 0.0))


class Pos:
    __slots__ = ['direction', 'entry']
    def __init__(self, direction, entry):
        self.direction = direction
        self.entry = entry


def run_lattice(symbol, bars, info, step_sell, step_buy, cap, fill_cap_pips=10.0):
    if not bars:
        return {}

    pip = pip_size_for(info)
    spread = spread_price(info)
    base_step_sell = step_sell * pip
    base_step_buy = step_buy * pip
    fill_cap_px = fill_cap_pips * pip

    anchor = bars[0]["close"]
    sell_level = anchor + base_step_sell
    buy_level = anchor - base_step_buy

    positions = [Pos("SELL", sell_level), Pos("BUY", buy_level)]
    realized = 0.0
    closes = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        oss = sum(1 for p in positions if p.direction == "SELL")
        obs = sum(1 for p in positions if p.direction == "BUY")

        while bar["high"] >= sell_level and oss < cap:
            positions.append(Pos("SELL", sell_level))
            oss += 1
            if oss >= 20:
                sell_level += base_step_sell * 2.0
            elif oss >= 10:
                sell_level += base_step_sell * 1.5
            else:
                sell_level += base_step_sell

        while bar["low"] <= buy_level and obs < cap:
            positions.append(Pos("BUY", buy_level))
            obs += 1
            if obs >= 20:
                buy_level -= base_step_buy * 2.0
            elif obs >= 10:
                buy_level -= base_step_buy * 1.5
            else:
                buy_level -= base_step_buy

        sells = sorted([p for p in positions if p.direction == "SELL"],
                       key=lambda p: p.entry, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry:
            raw_depth = sells[1].entry - bar["low"]
            capped_depth = min(raw_depth, fill_cap_px)
            fill_px = sells[1].entry - capped_depth
            pnl = pnl_usd(symbol, "SELL", sells[0].entry, fill_px, spread)
            if pnl <= 0:
                break
            realized += pnl
            closes += 1
            positions.remove(sells[0])
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)

        buys = sorted([p for p in positions if p.direction == "BUY"],
                      key=lambda p: p.entry)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry:
            raw_depth = bar["high"] - buys[1].entry
            capped_depth = min(raw_depth, fill_cap_px)
            fill_px = buys[1].entry + capped_depth
            pnl = pnl_usd(symbol, "BUY", buys[0].entry, fill_px, spread)
            if pnl <= 0:
                break
            realized += pnl
            closes += 1
            positions.remove(buys[0])
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)

    last_close = bars[-1]["close"]
    floating = [pnl_usd(symbol, p.direction, p.entry, last_close, spread) for p in positions]
    floating_net = sum(floating)
    worst = min(floating) if floating else 0.0

    return {
        "combined": round(realized + floating_net, 3),
        "realized": round(realized, 3),
        "floating": round(floating_net, 3),
        "worst": round(worst, 3),
        "closes": closes,
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 100)
    print("OUT-OF-SAMPLE VALIDATION — 30d test (days 61-90) on ultra-tight configs")
    print("=" * 100)

    # Ultra-tight configs from apex doubler
    configs = {
        "GBPUSD": (0.10, 0.10, 50),
        "EURUSD": (0.20, 0.20, 50),
        "NZDUSD": (0.15, 0.15, 50),
    }

    # Also validate the sharpened configs on 30d
    sharpened_configs = {
        "GBPUSD": (0.75, 1.00, 25),
        "EURUSD": (1.25, 2.00, 20),
        "NZDUSD": (0.25, 0.50, 50),
    }

    rows = []

    for symbol, (ss, bs, cap) in configs.items():
        info = mt5.symbol_info(symbol)
        # Load 90 days, slice to days 61-90
        bars_90 = load_bars(symbol, 90)
        if not bars_90 or info is None:
            print(f"  {symbol}: No data")
            continue
        if len(bars_90) < 500:
            print(f"  {symbol}: Too few bars ({len(bars_90)}), need ~40+ trading days")
            continue

        # Use 2/3 train, 1/3 OOS
        total_bars = len(bars_90)
        split = int(total_bars * 2 / 3)
        bars_train = bars_90[:split]
        bars_oos = bars_90[split:]
        train_days = round(len(bars_train) / 1440, 1)
        oos_days = round(len(bars_oos) / 1440, 1)
        print(f"\n=== {symbol} — ultra-tight step={ss} cap={cap} ===")
        print(f"  Training ({train_days}d): {len(bars_train)} bars | OOS ({oos_days}d): {len(bars_oos)} bars")

        if not bars_train or not bars_oos:
            print(f"  {symbol}: Insufficient data for OOS")
            continue

        # Training result
        r_train = run_lattice(symbol, bars_train, info, ss, bs, cap)
        daily_train = r_train["combined"] / (len(bars_train) / 1440)

        # OOS result (scaled to 60d equivalent)
        r_oos = run_lattice(symbol, bars_oos, info, ss, bs, cap)
        oos_days_actual = len(bars_oos) / 1440
        daily_oos = r_oos["combined"] / oos_days_actual
        oos_scaled_60d = r_oos["combined"] * (60 / oos_days_actual)

        print(f"  TRAINING  combined=${r_train['combined']:+8.2f} daily=${daily_train:+6.2f} "
              f"closes={r_train['closes']:>5} worst=${r_train['worst']:+7.2f}")
        print(f"  OOS ({oos_days:.0f}d) combined=${r_oos['combined']:+8.2f} daily=${daily_oos:+6.2f} "
              f"closes={r_oos['closes']:>5} worst=${r_oos['worst']:+7.2f}")
        print(f"  OOS scaled 60d=${oos_scaled_60d:+.2f}  retention={oos_scaled_60d/r_train['combined']*100:.0f}%")

        decay = 1 - (oos_scaled_60d / r_train["combined"]) if r_train["combined"] > 0 else 0
        if decay < 0:
            tag = "🔥 IMPROVED"
        elif decay < 0.3:
            tag = "✅ STRONG"
        elif decay < 0.5:
            tag = "⚠️ WEAKENED"
        else:
            tag = "❌ COLLAPSED"
        print(f"  Decay: {decay*100:.0f}%  {tag}")

        rows.append({
            "symbol": symbol, "config": "ultra_tight",
            "step_sell": ss, "step_buy": bs, "cap": cap,
            "train_60d": r_train["combined"], "train_daily": daily_train,
            "oos_30d": r_oos["combined"], "oos_daily": daily_oos,
            "oos_scaled_60d": oos_scaled_60d,
            "retention_pct": round(oos_scaled_60d/r_train["combined"]*100, 1) if r_train["combined"] > 0 else 0,
            "decay_pct": round(decay*100, 1),
            "train_worst": r_train["worst"], "oos_worst": r_oos["worst"],
            "train_closes": r_train["closes"], "oos_closes": r_oos["closes"],
        })

    # Sharpened configs
    # Check if we have enough data
    print(f"\n  NOTE: Using 2/3 train, 1/3 OOS split on available data")
    print(f"  (MT5 typically has ~60 trading days; using 40/20 split)")
    print(f"{'='*90}")
    print("SHARPENED CONFIGS — OOS validation (40d train / 20d OOS)")
    print(f"{'='*90}")

    for symbol, (ss, bs, cap) in sharpened_configs.items():
        info = mt5.symbol_info(symbol)
        bars_90 = load_bars(symbol, 90)
        if not bars_90 or info is None:
            continue

        total_bars = len(bars_90)
        if total_bars < 500:  # Less than ~1 day of data
            print(f"  {symbol}: Insufficient data ({total_bars} bars)")
            continue

        # Use 2/3 train, 1/3 OOS
        split = int(total_bars * 2 / 3)
        bars_train = bars_90[:split]
        bars_oos = bars_90[split:]
        train_days = round(len(bars_train) / 1440, 1)
        oos_days = round(len(bars_oos) / 1440, 1)

        print(f"\n  {symbol} — sharpened sell={ss} buy={bs} cap={cap} ({train_days}d train / {oos_days}d OOS)")

        r_train = run_lattice(symbol, bars_train, info, ss, bs, cap)
        daily_train = r_train["combined"] / train_days
        print(f"    TRAINING ({train_days}d) combined=${r_train['combined']:+8.2f} daily=${daily_train:+6.2f}")

        r_oos = run_lattice(symbol, bars_oos, info, ss, bs, cap)
        oos_days_actual = len(bars_oos) / 1440
        daily_oos = r_oos["combined"] / oos_days_actual if oos_days_actual > 0 else 0
        oos_scaled_60d = r_oos["combined"] * (60 / oos_days_actual) if oos_days_actual > 0 else 0
        print(f"    OOS ({oos_days:.0f}d) combined=${r_oos['combined']:+8.2f} daily=${daily_oos:+6.2f}")
        print(f"    OOS scaled 60d=${oos_scaled_60d:+.2f}  retention={oos_scaled_60d/r_train['combined']*100:.0f}%")

        decay = 1 - (oos_scaled_60d / r_train["combined"]) if r_train["combined"] > 0 else 0
        if decay < 0:
            tag = "🔥"
        elif decay < 0.3:
            tag = "✅"
        elif decay < 0.5:
            tag = "⚠️"
        else:
            tag = "❌"
        print(f"    Decay: {decay*100:.0f}%  {tag}")

        rows.append({
            "symbol": symbol, "config": "sharpened",
            "step_sell": ss, "step_buy": bs, "cap": cap,
            "train_60d": r_train["combined"], "train_daily": daily_train,
            "oos_30d": r_oos["combined"], "oos_daily": daily_oos,
            "oos_scaled_60d": oos_scaled_60d,
            "retention_pct": round(oos_scaled_60d/r_train["combined"]*100, 1) if r_train["combined"] > 0 else 0,
            "decay_pct": round(decay*100, 1),
            "train_worst": r_train["worst"], "oos_worst": r_oos["worst"],
            "train_closes": r_train["closes"], "oos_closes": r_oos["closes"],
        })

    # Save
    if not rows:
        print("\nNo rows collected — MT5 data insufficient for OOS split.")
        print("MT5 typically only loads ~40-50 trading days of M1 data.")
        print("Need more historical data for proper OOS validation.")
    else:
        output = ROOT / "reports" / "oos_validation.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nSaved {output}")

    # Summary
    print(f"\n{'='*90}")
    print("OOS SUMMARY")
    print(f"{'='*90}")

    for row in rows:
        print(f"  {row['symbol']:<8} {row['config']:<12} train=${row['train_60d']:+7.2f} "
              f"oos=${row['oos_scaled_60d']:+7.2f}  retention={row['retention_pct']:>4.0f}%  "
              f"decay={row['decay_pct']:+5.0f}%")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
