import json
from pathlib import Path
from datetime import datetime, timezone

def is_good_session(utc_str):
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return 7 <= dt.hour < 21
    except:
        return True

def main():
    log_file = Path("reports/penetration_lattice_shadow_fx_close_policy_mixed_events.jsonl")
    if not log_file.exists():
        print(f"File {log_file} does not exist.")
        return

    good_pnl = 0.0
    bad_pnl = 0.0
    good_count = 0
    bad_count = 0
    
    # We want to match live_rearm's symbols
    target_symbols = {"EURUSD", "GBPUSD"}

    with log_file.open("r") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                evt = json.loads(line)
            except:
                continue
            
            if evt.get("action") == "close_ticket" and evt.get("symbol") in target_symbols:
                ts_utc = evt.get("ts_utc")
                if not ts_utc:
                    continue
                
                pnl = evt.get("realized_pnl", 0.0)
                
                if is_good_session(ts_utc):
                    good_pnl += pnl
                    good_count += 1
                else:
                    bad_pnl += pnl
                    bad_count += 1

    print(f"=== Shadow Cool12 Alpha50 Analysis (EURUSD, GBPUSD) ===")
    print(f"Proxy for live_rearm without session gate")
    print(f"Good session (07-21 UTC): {good_count} closes, PnL: ${good_pnl:.2f}")
    print(f"Bad session (21-07 UTC):  {bad_count} closes, PnL: ${bad_pnl:.2f}")
    
    print("\nIf live_rearm is skipping the Bad session, it is:")
    if bad_pnl < 0:
        print(f"  SAVING MONEY (avoiding ${bad_pnl:.2f} loss)")
    else:
        print(f"  LOSING MONEY (missing out on ${bad_pnl:.2f} profit)")

if __name__ == "__main__":
    main()
