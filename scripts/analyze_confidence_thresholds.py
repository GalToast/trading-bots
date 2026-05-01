"""Analyze confidence threshold effectiveness across all fire modes."""
import json
from collections import defaultdict

# Load trade log
trades = []
with open("trade_behavior_log.jsonl", "r") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            trades.append(json.loads(line))
        except json.JSONDecodeError:
            pass

print(f"Total trades: {len(trades)}\n")

# 1. WR by confidence band
bands = {
    "0.00-0.39": (0.0, 0.40),
    "0.40-0.44": (0.40, 0.45),
    "0.45-0.49": (0.45, 0.50),
    "0.50-0.54": (0.50, 0.55),
    "0.55-0.59": (0.55, 0.60),
    "0.60-0.69": (0.60, 0.70),
    "0.70+":      (0.70, 1.01),
}

print("=== CONFIDENCE BAND ANALYSIS ===")
print(f"{'Band':<12} {'Trades':>7} {'Wins':>6} {'Losses':>8} {'WR':>7} {'Total PnL':>11} {'Avg PnL':>9}")
print("-" * 70)

for band_name, (lo, hi) in bands.items():
    band_trades = [t for t in trades if lo <= (t.get("entry_confidence_raw") or 0) < hi]
    wins = [t for t in band_trades if (t.get("realized_pnl") or 0) > 0]
    losses = [t for t in band_trades if (t.get("realized_pnl") or 0) <= 0]
    total_pnl = sum(t.get("realized_pnl") or 0 for t in band_trades)
    avg_pnl = total_pnl / len(band_trades) if band_trades else 0
    wr = len(wins) / len(band_trades) * 100 if band_trades else 0
    
    print(f"{band_name:<12} {len(band_trades):>7} {len(wins):>6} {len(losses):>8} {wr:>6.1f}% ${total_pnl:>10.2f} ${avg_pnl:>8.2f}")

# 2. WR by entry mode
print("\n=== ENTRY MODE ANALYSIS ===")
modes = defaultdict(list)
for t in trades:
    mode = t.get("entry_mode", "UNKNOWN")
    modes[mode].append(t)

print(f"{'Mode':<18} {'Trades':>7} {'Wins':>6} {'Losses':>8} {'WR':>7} {'Total PnL':>11} {'Avg PnL':>9} {'Avg Conf':>10}")
print("-" * 85)

for mode, mode_trades in sorted(modes.items(), key=lambda x: -sum(t.get("realized_pnl") or 0 for t in x[1])):
    wins = [t for t in mode_trades if (t.get("realized_pnl") or 0) > 0]
    losses = [t for t in mode_trades if (t.get("realized_pnl") or 0) <= 0]
    total_pnl = sum(t.get("realized_pnl") or 0 for t in mode_trades)
    avg_pnl = total_pnl / len(mode_trades) if mode_trades else 0
    avg_conf = sum(t.get("entry_confidence_raw") or 0 for t in mode_trades) / len(mode_trades) if mode_trades else 0
    wr = len(wins) / len(mode_trades) * 100 if mode_trades else 0
    
    print(f"{mode:<18} {len(mode_trades):>7} {len(wins):>6} {len(losses):>8} {wr:>6.1f}% ${total_pnl:>10.2f} ${avg_pnl:>8.2f} {avg_conf:>10.3f}")

# 3. Mode-specific: What if we raised RAW threshold?
print("\n=== RAW LANE THRESHOLD SWEEP (what-if) ===")
raw_trades = modes.get("RAW", [])
raw_trades.sort(key=lambda t: t.get("entry_confidence_raw") or 0, reverse=True)

for threshold in [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
    filtered = [t for t in raw_trades if (t.get("entry_confidence_raw") or 0) >= threshold]
    wins = [t for t in filtered if (t.get("realized_pnl") or 0) > 0]
    total_pnl = sum(t.get("realized_pnl") or 0 for t in filtered)
    wr = len(wins) / len(filtered) * 100 if filtered else 0
    
    print(f"  RAW >= {threshold:.2f}: {len(filtered):>4} trades, {len(wins):>3} wins, {wr:>5.1f}% WR, ${total_pnl:>10.2f} total")

# 4. Mode-specific: What if we raised PRICE threshold?
print("\n=== PRICE LANE THRESHOLD SWEEP (what-if) ===")
price_trades = modes.get("PRICE", [])
price_trades.sort(key=lambda t: t.get("entry_confidence_raw") or 0, reverse=True)

for threshold in [0.55, 0.60, 0.65, 0.70]:
    filtered = [t for t in price_trades if (t.get("entry_confidence_raw") or 0) >= threshold]
    wins = [t for t in filtered if (t.get("realized_pnl") or 0) > 0]
    total_pnl = sum(t.get("realized_pnl") or 0 for t in filtered)
    wr = len(wins) / len(filtered) * 100 if filtered else 0
    
    print(f"  PRICE >= {threshold:.2f}: {len(filtered):>4} trades, {len(wins):>3} wins, {wr:>5.1f}% WR, ${total_pnl:>10.2f} total")

# 5. Revenue-maximizing threshold
print("\n=== REVENUE OPTIMIZATION ===")
all_trades_sorted = sorted(trades, key=lambda t: t.get("entry_confidence_raw") or 0, reverse=True)

best_pnl = float('-inf')
best_threshold = 0
for threshold_100 in range(40, 80, 5):
    threshold = threshold_100 / 100
    filtered = [t for t in all_trades_sorted if (t.get("entry_confidence_raw") or 0) >= threshold]
    total_pnl = sum(t.get("realized_pnl") or 0 for t in filtered)
    if total_pnl > best_pnl:
        best_pnl = total_pnl
        best_threshold = threshold

print(f"  Revenue-maximizing threshold: {best_threshold:.2f}")
print(f"  Total PnL at that threshold: ${best_pnl:.2f}")
print(f"  Trades at that threshold: {len([t for t in all_trades_sorted if (t.get('entry_confidence_raw') or 0) >= best_threshold])}")
