import json
import time
from datetime import datetime
from pathlib import Path
import subprocess

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR
for candidate in (CURRENT_DIR, *CURRENT_DIR.parents):
    if (candidate / "runtime_state.json").exists() and (candidate / "mt5_bot_v10.py").exists():
        ROOT_DIR = candidate
        break

RUNTIME_STATE_FILE = ROOT_DIR / "runtime_state.json"
WORKER_LOG_FILE = ROOT_DIR / "mt5_canonical_worker_out.log"

print('Starting 10-minute monitoring cycle...')
print('Collecting: entries, exits, posture changes, blocking patterns')
print('=' * 60)

# Track initial state
initial_positions = None
entries = []
exits = []
posture_changes = []
blocking_patterns = []

# Check log for entries/exits
def check_log_for_trades(lines):
    trades = []
    for line in lines:
        if 'OPEN [' in line:
            trades.append(('OPEN', line))
        elif 'CLOSE [' in line or 'TRIM' in line or 'DERISK' in line or 'WINBAG' in line:
            trades.append(('CLOSE', line))
    return trades

start_time = time.time()
last_log_size = 0

for cycle in range(20):  # 20 cycles over 10 minutes
    time.sleep(30)
    
    try:
        # Read state
        with RUNTIME_STATE_FILE.open('r', encoding='utf-8') as f:
            state = json.load(f)
        
        current_time = datetime.now().strftime('%H:%M:%S')
        positions = state['managed_positions']
        equity = state['equity']
        posture = state['entry_posture']
        rearm = state['rearm_active']
        dd_pct = state['managed_drawdown_pct']
        fm_ratio = state['free_margin_ratio']
        
        # Track posture changes
        if posture_changes and posture_changes[-1]['posture'] != posture:
            posture_changes.append({
                'time': current_time,
                'posture': posture,
                'rearm': rearm
            })
        elif not posture_changes:
            posture_changes.append({
                'time': current_time,
                'posture': posture,
                'rearm': rearm
            })
        
        # Track position count changes
        if initial_positions is None:
            initial_positions = positions
        elif positions != initial_positions:
            if positions > initial_positions:
                entries.append(current_time)
            else:
                exits.append(current_time)
            initial_positions = positions
        
        print(f"[{current_time}] Pos:{positions} Eq:${equity:.0f} {posture} {'REARM' if rearm else ''} DD:{dd_pct*100:.1f}% FM:{fm_ratio*100:.0f}%")
        
        # Check log for new trades every 2 cycles
        if cycle % 2 == 0:
            try:
                with WORKER_LOG_FILE.open('r', encoding='utf-8') as f:
                    log_content = f.read()
                    current_size = len(log_content)
                    
                    if current_size > last_log_size:
                        new_content = log_content[last_log_size:]
                        new_lines = new_content.split('\n')
                        trades = check_log_for_trades(new_lines)
                        for trade_type, line in trades:
                            if trade_type == 'OPEN':
                                print(f"  >>> ENTRY: {line.strip()}")
                            else:
                                print(f"  >>> EXIT: {line.strip()}")
                        last_log_size = current_size
            except:
                pass
                
    except Exception as e:
        print(f'Error reading state: {e}')

elapsed = time.time() - start_time
print(f'\n{"=" * 60}')
print(f'Monitoring complete ({elapsed/60:.1f} minutes)')
print(f'Posture changes: {len(posture_changes)}')
print(f'Entries detected: {len(entries)}')
print(f'Exits detected: {len(exits)}')
