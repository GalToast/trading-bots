import json
import os
import sys
import time
from datetime import datetime, timezone
import subprocess

def check_syntax(file_path):
    print(f"Checking syntax of {file_path}...")
    result = subprocess.run([sys.executable, "-m", "py_compile", file_path], capture_output=True)
    if result.returncode != 0:
        print(f"❌ SYNTAX ERROR in {file_path}:")
        print(result.stderr.decode('utf-8'))
        return False
    print(f"✅ {file_path} syntax is valid.")
    return True

def check_runtime_state(file_path):
    print(f"\nChecking runtime state in {file_path}...")
    if not os.path.exists(file_path):
        print(f"❌ State file {file_path} not found.")
        return False
    
    try:
        with open(file_path, 'r') as f:
            state = json.load(f)
            
        positions = state.get("managed_positions", 0) + state.get("adopted_positions", 0) + state.get("direct_positions", 0)
        if positions > 0:
            print(f"❌ Bot still has {positions} open positions. WAIT FOR FLAT WINDOW.")
            return False
            
        print(f"✅ 0 open positions confirmed.")
        
        # Check if heartbeat is recent
        updated_str = state.get("updated_at")
        if updated_str:
            try:
                updated_at = datetime.fromisoformat(updated_str.replace('Z', '+00:00'))
                now_utc = datetime.now(timezone.utc)
                diff = (now_utc - updated_at).total_seconds()
                if diff > 120:
                    print(f"⚠️ Warning: Last heartbeat was {diff:.1f} seconds ago. Bot might be stuck.")
                else:
                    print(f"✅ Heartbeat is fresh ({diff:.1f}s ago).")
            except Exception as e:
                print(f"⚠️ Could not parse updated_at: {e}")
                
        return True
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False

def check_log_errors(log_file):
    print(f"\nChecking for recent fatal errors in {log_file}...")
    if not os.path.exists(log_file):
        print(f"⚠️ Log file {log_file} not found. Skipping.")
        return True
        
    try:
        # Check if log is stale
        mtime = os.path.getmtime(log_file)
        now = time.time()
        diff = now - mtime
        if diff > 900: # 15 minutes
            print(f"✅ Log file {log_file} is stale ({diff:.1f}s old). Ignoring historical exceptions.")
            return True
            
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()[-20:]
            err_count = sum(1 for line in lines if "Exception" in line or "Traceback" in line)
            if err_count > 0:
                print(f"❌ Found recent exceptions in {log_file}. Bot may be crash-looping.")
                for line in lines:
                    if "Exception" in line or "Traceback" in line:
                        print(f"  > {line.strip()}")
                return False
        print(f"✅ No recent fatal errors detected in logs.")
        return True
    except Exception as e:
        print(f"⚠️ Error reading {log_file}: {e}")
        return True

def main():
    print("========================================")
    print("      PRE-RELOAD SAFETY CHECKLIST       ")
    print("========================================")
    
    # Paths based on root directory
    bot_script = "mt5_bot_v10.py"
    state_file = "runtime_state.json"
    err_log = "mt5_canonical_worker_err.log"
    
    success = True
    
    if not check_syntax(bot_script): success = False
    if not check_runtime_state(state_file): success = False
    if not check_log_errors(err_log): success = False
    
    print("\n========================================")
    if success:
        print("✅ ALL CHECKS PASSED. SAFE TO RELOAD.")
        sys.exit(0)
    else:
        print("❌ CHECKS FAILED. DO NOT RELOAD.")
        sys.exit(1)

if __name__ == "__main__":
    main()
