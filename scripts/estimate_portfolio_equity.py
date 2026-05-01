import json
from pathlib import Path
import datetime

REPORTS = Path("reports")
result_files = [
    "reports/gbpusd_m1_cer.json",
    "reports/eurusd_m1_cer.json"
]

def load_results(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return []

def main():
    # This is a conceptual aggregator
    # Real portfolio equity needs bar-by-bar time sync
    # For now, we'll estimate based on the min/max stats
    
    gbp = load_results("reports/gbpusd_m1_cer.json")
    eur = load_results("reports/eurusd_m1_cer.json")
    
    if not gbp or not eur:
        print("Missing result files.")
        return

    g = gbp[0]
    e = eur[0]
    
    # Simple sum (pessimistic - assumes peaks align)
    total_hr = g["realized_usd_per_hour"] + e["realized_usd_per_hour"]
    total_mae = g["max_adverse_excursion_usd"] + e["max_adverse_excursion_usd"]
    
    print("# CER PORTFOLIO EQUITY SNAPSHOT\n")
    print(f"| Metric | GBPUSD | EURUSD | COMBINED (Peak-Aligned) |")
    print(f"|--------|--------|--------|------------------------|")
    print(f"| $/hr   | ${g['realized_usd_per_hour']:.2f} | ${e['realized_usd_per_hour']:.2f} | **${total_hr:.2f}** |")
    print(f"| Max MAE | ${g['max_adverse_excursion_usd']:.2f} | ${e['max_adverse_excursion_usd']:.2f} | **${total_mae:.2f}** |")
    print(f"| Wins   | {g['wins']} | {e['wins']} | {g['wins']+e['wins']} |")

    print("\nNext step: Build a true time-synchronized portfolio engine to prove the 'Pair Offset' alpha.")

if __name__ == "__main__":
    main()
