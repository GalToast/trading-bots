#!/usr/bin/env python3
"""Compute FX scale equivalents for BTC M15 $15 and $20 steps."""
import MetaTrader5 as mt5

mt5.initialize()

btc_info = mt5.symbol_info("BTCUSD")
btc_price = btc_info.bid if btc_info else 75000.0
print(f"BTCUSD price: {btc_price:.2f}")

btc_steps = [15.0, 20.0]
for s in btc_steps:
    pct = s / btc_price * 100
    print(f"  ${s} step = {pct:.4f}% of price")

print()
print(f"{'Symbol':<10} {'Price':>12} {'Step':>12} {'Pips':>8} {'Spread':>8} {'S/Step':>8}")
print("-" * 65)

fx_symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
for sym in fx_symbols:
    info = mt5.symbol_info(sym)
    if info is None:
        print(f"{sym}: NOT AVAILABLE")
        continue
    price = info.bid
    digits = int(info.digits or 5)
    point = float(info.point or 0.00001)
    pip = point * (10.0 if digits in (3, 5) else 1.0)
    spread_px = info.spread * point

    for btc_step in btc_steps:
        pct = btc_step / btc_price
        fx_step = price * pct
        pips = fx_step / pip
        spread_ratio = spread_px / fx_step if fx_step > 0 else 0
        label = f"${btc_step:.0f}"
        print(f"{sym:<10} {price:>12.5f} {fx_step:>12.6f} {pips:>8.1f} {spread_px:>8.5f} {spread_ratio:>8.3f}")
    print()

mt5.shutdown()
