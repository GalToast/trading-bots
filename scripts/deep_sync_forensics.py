"""
Deep SYNC_CLOSE Forensic Tool
============================
Provides the causal proof requested by Codex regarding SYNC_CLOSE losses.
Breaks down the bucket by trigger source, adoption, and symbol.
"""
import json
import os
import pandas as pd
import sys

# Set encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def analyze():
    log_path = "trade_behavior_log.jsonl"
    if not os.path.exists(log_path):
        print("Error: log not found.")
        return

    trades = []
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                trades.append(json.loads(line))
            except: continue
            
    if not trades:
        print("No trades found.")
        return

    df = pd.DataFrame(trades)
    
    # Standardize column names
    if 'realized_pnl' in df.columns:
        df['pnl'] = pd.to_numeric(df['realized_pnl'], errors='coerce')
    elif '_pnl' in df.columns:
        df['pnl'] = df['_pnl']
    else:
        print("Error: P/L column not found.")
        return

    # Filter for SYNC_CLOSE
    sync_df = df[df['exit_reason'].str.startswith('SYNC_CLOSE', na=False)].copy()

    print("\n=== DEEP SYNC_CLOSE FORENSIC REPORT ===")
    if sync_df.empty:
        print("No SYNC_CLOSE events found.")
        return

    total_pnl = sync_df['pnl'].sum()
    print(f"Total SYNC_CLOSE Bucket: {len(sync_df)} trades | Total P/L: ${total_pnl:+.2f}")

    # 1. By Trigger Source
    def get_source(reason):
        reason = str(reason).upper()
        if 'CLEANUP' in reason: return 'CLEANUP_LANE'
        if 'MAIN_LOOP_SYNC' in reason or 'SOURCE=MAIN_LOOP_SYNC' in reason: return 'MAIN_LOOP_SYNC'
        return 'OTHER_SYNC'
    
    sync_df['trigger_source'] = sync_df['exit_reason'].apply(get_source)
    print("\n[1] BY TRIGGER SOURCE:")
    print(sync_df.groupby('trigger_source')['pnl'].agg(['count', 'sum', 'mean']).sort_values('sum'))

    # 2. By Adoption
    if 'adopted' in sync_df.columns:
        print("\n[2] BY ADOPTION STATUS:")
        print(sync_df.groupby('adopted')['pnl'].agg(['count', 'sum', 'mean']))

    # 3. By Mode
    if 'entry_mode' in sync_df.columns:
        print("\n[3] BY ENTRY MODE:")
        print(sync_df.groupby('entry_mode')['pnl'].agg(['count', 'sum', 'mean']).sort_values('sum'))

    # 4. Top Symbol Killers
    print("\n[4] TOP 5 SYMBOL KILLERS (SYNC_CLOSE ONLY):")
    print(sync_df.groupby('symbol')['pnl'].sum().sort_values().head(5))

if __name__ == "__main__":
    analyze()
