"""Analyze EURUSD live vs backtest performance gap."""
import json
from collections import defaultdict

# Load trade behavior log
eurusd_trades = []
all_symbols = defaultdict(int)

with open("trade_behavior_log.jsonl", "r") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            trade = json.loads(line)
            sym = trade.get("symbol", "UNKNOWN")
            all_symbols[sym] += 1
            if sym == "EURUSD":
                eurusd_trades.append(trade)
        except json.JSONDecodeError:
            pass

print(f"Total symbols in trade log: {dict(all_symbols)}")
print(f"EURUSD trades in log: {len(eurusd_trades)}")

if not eurusd_trades:
    print("No EURUSD trades found in trade_behavior_log.jsonl")
    print("The log may use different symbol encoding or the file is stale.")
    
    # Check the first few entries for symbol format
    with open("trade_behavior_log.jsonl", "r") as f:
        for i, line in enumerate(f):
            if i >= 5:
                break
            try:
                trade = json.loads(line.strip())
                print(f"  Sample trade keys: {list(trade.keys())}")
                print(f"  symbol field: {trade.get('symbol', 'NO SYMBOL FIELD')}")
            except:
                pass
    exit(0)

# Analyze EURUSD trades
exit_reasons = defaultdict(int)
modes = defaultdict(int)
directions = defaultdict(int)
total_pnl = 0.0
wins, losses = [], []
hold_times = []
spread_at_entry = []

for t in eurusd_trades:
    pnl = t.get("realized_pnl", 0) or 0
    total_pnl += pnl
    if pnl > 0:
        wins.append(pnl)
    else:
        losses.append(pnl)
    
    exit_reasons[t.get("exit_reason", "UNKNOWN")] += 1
    modes[t.get("mode", "UNKNOWN")] += 1
    directions[t.get("direction", "UNKNOWN")] += 1
    hold_times.append(t.get("hold_seconds", 0))
    
    spread = t.get("spread_at_entry")
    if spread is not None:
        spread_at_entry.append(spread)

print(f"\n=== EURUSD ANALYSIS ===")
print(f"Total trades: {len(eurusd_trades)}")
print(f"Total PnL: ${total_pnl:.2f}")
print(f"Wins: {len(wins)}, Losses: {len(losses)}")
if wins:
    print(f"Avg win: ${sum(wins)/len(wins):.2f}")
if losses:
    print(f"Avg loss: ${sum(losses)/len(losses):.2f}")
print(f"WR: {len(wins)/len(eurusd_trades)*100:.1f}%")

print(f"\nExit reasons:")
for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
    print(f"  {reason}: {count}")

print(f"\nModes:")
for mode, count in sorted(modes.items(), key=lambda x: -x[1]):
    print(f"  {mode}: {count}")

print(f"\nDirections:")
for d, count in sorted(directions.items(), key=lambda x: -x[1]):
    print(f"  {d}: {count}")

print(f"\nHold time stats:")
if hold_times:
    print(f"  Avg: {sum(hold_times)/len(hold_times):.0f}s")
    print(f"  Min: {min(hold_times)}s, Max: {max(hold_times)}s")
    quick_deaths = [h for h in hold_times if h < 120]
    print(f"  <120s holds: {len(quick_deaths)}")

print(f"\nSpread at entry:")
if spread_at_entry:
    print(f"  Avg: {sum(spread_at_entry)/len(spread_at_entry):.6f}")
    print(f"  Min: {min(spread_at_entry):.6f}, Max: {max(spread_at_entry):.6f}")

# Load symbol_learner data for EURUSD
with open("symbol_learner.json", "r") as f:
    learner = json.load(f)

print(f"\n=== SYMBOL LEARNER (simple state) ===")
eurusd_simple = learner.get("EURUSD", {})
print(f"  last_pnl: {eurusd_simple.get('last_pnl', 'N/A')}")
print(f"  wins: {eurusd_simple.get('wins', 'N/A')}")
print(f"  losses: {eurusd_simple.get('losses', 'N/A')}")
print(f"  atr_multiplier: {eurusd_simple.get('atr_multiplier', 'N/A')}")
print(f"  confidence_bump: {eurusd_simple.get('confidence_bump', 'N/A')}")

# Check the richer EURUSD state
eurusd_rich = learner.get("symbols", {}).get("EURUSD", {})
if eurusd_rich:
    print(f"\n=== SYMBOL LEARNER (rich state) ===")
    print(f"  avg_atr_at_entry: {eurusd_rich.get('avg_atr_at_entry', 'N/A')}")
    print(f"  consecutive_losses: {eurusd_rich.get('consecutive_losses', 'N/A')}")
    print(f"  consecutive_wins: {eurusd_rich.get('consecutive_wins', 'N/A')}")
    print(f"  last_mode: {eurusd_rich.get('last_mode', 'N/A')}")
    print(f"  failure_modes: {eurusd_rich.get('failure_modes', {})}")
    mode_stats = eurusd_rich.get("mode_stats", {})
    if mode_stats:
        print(f"  mode_stats:")
        for mode, stats in mode_stats.items():
            print(f"    {mode}: {stats}")
