import subprocess
import json
import sys
from pathlib import Path

# Add scripts to path
sys.path.insert(0, str(Path("scripts").resolve()))

def run_backtest(symbol, timeframe, step, mode, portfolio, hedge, cap, mae_limit=None):
    root = Path(__file__).parent.parent
    backtest_script = root / "scripts" / "backtest_snake_counter_web.py"
    cmd = [
        "python", str(backtest_script),
        "--symbols", symbol,
        "--days", "5",
        "--timeframe", timeframe,
        "--step-pips", str(step),
        "--retrace-steps", "1",
        "--hold-frontier", "0",
        "--controller-modes", mode,
        "--max-open-per-side-values", str(cap),
        "--portfolio-close-modes", portfolio,
        "--hedge-modes", hedge,
        "--output-json", f"tmp_hybrid_{symbol}_{timeframe}.json"
    ]
    if mae_limit:
        cmd.extend(["--max-mae-abs-usd", str(mae_limit)])
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error running {symbol} {timeframe}: {res.stderr}")
    
    try:
        with open(f"tmp_hybrid_{symbol}_{timeframe}.json") as f:
            data = json.load(f)
            return data[0] if data else None
    except:
        return None

def main():
    symbols = ["GBPUSD", "EURUSD"]
    
    print("# M1/M15 HYBRID STACK TOURNAMENT\n")
    print("| Symbol | M1 $/hr (CER) | M15 $/hr (Cascade) | TOTAL $/hr | Combined MAE |")
    print("|--------|---------------|--------------------|------------|--------------|")
    
    for sym in symbols:
        # M1: Bootstrap Alpha (Elastic + Convergent Unwind + Same-Level Hedge)
        m1 = run_backtest(sym, "M1", 0.05, "gemini_elastic", "convergent_unwind", "same_level", 300)
        
        # M15: Cascade (Static + None + None)
        # Note: M15 doesn't have a separate "cascade" mode in snake_counter_web, 
        # but retrace=1 on M15 is close enough to bar extreme for comparison.
        m15 = run_backtest(sym, "M15", 0.5, "static", "none", "none", 32)
        
        if m1 and m15:
            total_hr = m1["realized_usd_per_hour"] + m15["realized_usd_per_hour"]
            comb_mae = m1["max_adverse_excursion_usd"] + m15["max_adverse_excursion_usd"]
            print(f"| {sym} | ${m1['realized_usd_per_hour']:.2f} | ${m15['realized_usd_per_hour']:.2f} | **${total_hr:.2f}** | ${comb_mae:.2f} |")
        else:
            print(f"| {sym} | error | error | - | - |")

if __name__ == "__main__":
    main()
