import json
from datetime import datetime

def analyze_painful_syncs():
    with open('trade_behavior_log.jsonl', 'r') as f:
        trades = [json.loads(line) for line in f]
    
    painful = [t for t in trades if t.get('realized_pnl', 0) < -100 and t.get('exit_reason','').startswith('SYNC_CLOSE')]
    
    print(f"{'Time (UTC)':<30} | {'Symbol':<8} | {'PnL':<10} | {'Reason'}")
    print("-" * 80)
    for t in painful:
        print(f"{t.get('recorded_at_utc', 'N/A'):<30} | {t.get('symbol', 'N/A'):<8} | {t.get('realized_pnl', 0):<10.2f} | {t.get('exit_reason', 'N/A')}")

if __name__ == "__main__":
    analyze_painful_syncs()
