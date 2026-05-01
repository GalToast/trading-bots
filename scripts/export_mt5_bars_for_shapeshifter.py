#!/usr/bin/env python3
"""Export MT5 bars to JSON for shapeshifter v2 backtesting."""
import json
import sys
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SYMBOLS = ["NAS100", "EURUSD", "GBPUSD", "US30", "ETHUSD", "BTCUSD"]
TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
}

def export_symbol(symbol: str, timeframe: str = "M15", bars: int = 2000):
    tf = TIMEFRAMES.get(timeframe, mt5.TIMEFRAME_M15)
    if not mt5.initialize():
        print(f"MT5 init failed")
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print(f"  No data for {symbol} {timeframe}")
        return None

    candles = []
    for r in rates:
        candles.append({
            "start": int(r["time"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
        })

    output_path = REPORTS / f"{symbol.lower()}_{timeframe.lower()}_bars.json"
    with open(output_path, "w") as f:
        json.dump({"symbol": symbol, "timeframe": timeframe, "count": len(candles), "candles": candles}, f)

    print(f"  Exported {len(candles)} {timeframe} bars for {symbol} → {output_path}")
    return output_path


if __name__ == "__main__":
    print("=== Exporting MT5 bars for shapeshifter v2 backtest ===")
    if not mt5.initialize():
        print("ERROR: MT5 initialization failed. Is MT5 running?")
        sys.exit(1)
    mt5.shutdown()

    for sym in SYMBOLS:
        export_symbol(sym, "M15", 2000)

    print("\nDone.")
