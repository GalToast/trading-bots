#!/usr/bin/env python
"""M5 Warp Symbol Viability Checker

Determines whether a symbol is viable for M5 Warp penetration lattice
based on spread/step ratio analysis.

A symbol is VIABLE if:
- Spread as % of step <= 30% (grid fills reliably)
- ATR is non-zero (volatility exists)

A symbol is MARGINAL if:
- Spread as % of step is 30-60% (fills intermittently)

A symbol is NOT VIABLE if:
- Spread as % of step > 60% (spread blocks fills)

Usage:
    python scripts/check_m5_symbol_viability.py              # Check all common symbols
    python scripts/check_m5_symbol_viability.py --symbol SOLUSD  # Check one symbol
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
REGISTRY_FILE = ROOT / "configs" / "penetration_lattice_runner_registry.json"

# Spread/step ratio thresholds
VIABLE_MAX = 0.30    # <= 30% spread/step = viable
MARGINAL_MAX = 0.60  # 30-60% = marginal
# > 60% = not viable

# Common symbols to check
COMMON_SYMBOLS = [
    # Crypto
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD", "LTCUSD", "DOGEUSD",
    # FX majors
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    # FX crosses
    "EURJPY", "GBPJPY", "AUDUSD", "NZDUSD", "AUDCAD", "NZDCAD", "USDCAD",
    # FX exotics
    "EURHKD", "USDHKD",
    # Indices
    "NAS100", "US30", "SPX500",
    # Commodities
    "XAUUSD", "XAGUSD",
]


def get_mt5_symbol_info(symbol):
    """Get symbol info from MT5 (spread, ATR, tick value)."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        
        # Get ATR from M5 bars
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 20)
        atr = 0.0
        if rates is not None and len(rates) >= 15:
            # Simple ATR calculation
            trs = []
            for i in range(1, min(15, len(rates))):
                high = rates[i]['high']
                low = rates[i]['low']
                prev_close = rates[i-1]['close']
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            atr = sum(trs) / len(trs) if trs else 0.0
        
        return {
            "symbol": symbol,
            "spread": info.spread * info.trade_tick_size,  # Spread in price units
            "spread_points": info.spread,
            "tick_size": info.trade_tick_size,
            "tick_value": info.trade_tick_value,
            "point": info.point,
            "atr_m5": atr,
            "step_1.0x_atr": atr * 1.0,
            "step_1.5x_atr": atr * 1.5,
        }
    except ImportError:
        return None
    except Exception as e:
        print(f"Error getting info for {symbol}: {e}", file=sys.stderr)
        return None


def estimate_spread(symbol):
    """Estimate typical spread for a symbol when MT5 is not available."""
    # Approximate spreads in price units (based on typical broker spreads)
    spreads = {
        "BTCUSD": 174.0,
        "ETHUSD": 3.30,
        "SOLUSD": 0.22,
        "XRPUSD": 0.0018,
        "ADAUSD": 0.0005,
        "LTCUSD": 0.10,
        "DOGEUSD": 0.00003,
        "EURUSD": 0.00010,
        "GBPUSD": 0.00013,
        "USDJPY": 0.014,
        "USDCHF": 0.00012,
        "EURJPY": 0.016,
        "GBPJPY": 0.020,
        "AUDUSD": 0.00012,
        "NZDUSD": 0.00015,
        "AUDCAD": 0.00018,
        "NZDCAD": 0.00022,
        "USDCAD": 0.00018,
        "EURHKD": 0.00050,
        "USDHKD": 0.00050,
        "NAS100": 0.90,
        "US30": 1.50,
        "SPX500": 1.00,
        "XAUUSD": 0.26,
        "XAGUSD": 0.02,
    }
    return spreads.get(symbol.upper(), None)


def estimate_atr(symbol):
    """Estimate typical M5 ATR for a symbol when MT5 is not available."""
    atrs = {
        "BTCUSD": 116.0,
        "ETHUSD": 2.20,
        "SOLUSD": 0.147,
        "XRPUSD": 0.0073,
        "ADAUSD": 0.0004,
        "LTCUSD": 0.04,
        "DOGEUSD": 0.00002,
        "EURUSD": 0.00018,
        "GBPUSD": 0.00022,
        "USDJPY": 0.0338,
        "USDCHF": 0.00017,
        "EURJPY": 0.026,
        "GBPJPY": 0.034,
        "AUDUSD": 0.00022,
        "NZDUSD": 0.00020,
        "AUDCAD": 0.00024,
        "NZDCAD": 0.00020,
        "USDCAD": 0.00026,
        "EURHKD": 0.0035,
        "USDHKD": 0.0035,
        "NAS100": 16.5,
        "US30": 27.1,
        "SPX500": 5.5,
        "XAUUSD": 4.74,
        "XAGUSD": 0.15,
    }
    return atrs.get(symbol.upper(), None)


def check_viability(symbol, spread=None, atr=None):
    """Check if a symbol is viable for M5 Warp at 1.5x ATR step."""
    if spread is None:
        spread = estimate_spread(symbol)
    if atr is None:
        atr = estimate_atr(symbol)
    
    if spread is None or atr is None:
        return {
            "symbol": symbol,
            "status": "UNKNOWN",
            "spread": spread,
            "atr": atr,
            "step_1.5x": None,
            "spread_step_pct": None,
            "note": "No data available",
        }
    
    step_1_5x = atr * 1.5
    step_1_0x = atr * 1.0
    pct_1_5x = (spread / step_1_5x) * 100 if step_1_5x > 0 else float('inf')
    pct_1_0x = (spread / step_1_0x) * 100 if step_1_0x > 0 else float('inf')
    
    if pct_1_5x <= VIABLE_MAX * 100:
        status = "VIABLE"
        note = f"Spread is {pct_1_5x:.0f}% of 1.5x ATR step — grid will fill reliably"
    elif pct_1_5x <= MARGINAL_MAX * 100:
        status = "MARGINAL"
        note = f"Spread is {pct_1_5x:.0f}% of 1.5x ATR step — fills intermittently"
    else:
        status = "NOT VIABLE"
        note = f"Spread is {pct_1_5x:.0f}% of 1.5x ATR step — spread blocks fills"
    
    return {
        "symbol": symbol,
        "status": status,
        "spread": spread,
        "atr": atr,
        "step_1_5x": round(step_1_5x, 6),
        "step_1_0x": round(step_1_0x, 6),
        "spread_step_pct_1_5x": round(pct_1_5x, 1),
        "spread_step_pct_1_0x": round(pct_1_0x, 1),
        "note": note,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="M5 Warp Symbol Viability Checker")
    parser.add_argument("--symbol", type=str, help="Check a specific symbol")
    parser.add_argument("--mt5", action="store_true", help="Use live MT5 data (otherwise uses estimates)")
    args = parser.parse_args()
    
    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = COMMON_SYMBOLS
    
    results = []
    for symbol in symbols:
        if args.mt5:
            info = get_mt5_symbol_info(symbol)
            if info:
                result = check_viability(symbol, spread=info["spread"], atr=info["atr_m5"])
            else:
                result = check_viability(symbol)
        else:
            result = check_viability(symbol)
        results.append(result)
    
    # Print table
    print("=" * 100)
    print("M5 WARP SYMBOL VIABILITY (1.5x ATR step)")
    print("=" * 100)
    print(f"{'Symbol':<12} {'Status':<12} {'Spread':>10} {'ATR':>10} {'Step 1.5x':>10} {'Spd/Step':>8} {'Verdict'}")
    print("-" * 100)
    
    # Sort by status
    status_order = {"VIABLE": 0, "MARGINAL": 1, "NOT VIABLE": 2, "UNKNOWN": 3}
    results.sort(key=lambda r: status_order.get(r["status"], 99))
    
    for r in results:
        spread_str = f"{r['spread']:.4f}" if r["spread"] else "—"
        atr_str = f"{r['atr']:.4f}" if r["atr"] else "—"
        step_str = f"{r['step_1_5x']:.4f}" if r["step_1_5x"] else "—"
        pct_str = f"{r['spread_step_pct_1_5x']:.0f}%" if r["spread_step_pct_1_5x"] is not None else "—"
        print(f"{r['symbol']:<12} {r['status']:<12} {spread_str:>10} {atr_str:>10} {step_str:>10} {pct_str:>8} {r['note']}")
    
    print("-" * 100)
    viable = [r for r in results if r["status"] == "VIABLE"]
    marginal = [r for r in results if r["status"] == "MARGINAL"]
    not_viable = [r for r in results if r["status"] == "NOT VIABLE"]
    
    print(f"\nSummary:")
    print(f"  VIABLE ({len(viable)}): {', '.join(r['symbol'] for r in viable)}")
    print(f"  MARGINAL ({len(marginal)}): {', '.join(r['symbol'] for r in marginal)}")
    print(f"  NOT VIABLE ({len(not_viable)}): {', '.join(r['symbol'] for r in not_viable)}")
    print(f"\nRule: Don't launch M5 Warp on any symbol with spread/step > 30%")
    print("=" * 100)


if __name__ == "__main__":
    main()
