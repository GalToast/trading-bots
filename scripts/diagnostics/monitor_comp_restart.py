"""Monitor COMP M5 quarantine lift and verify restart at 17:05 UTC."""
import json, time, sys
from datetime import datetime, timezone

QUARANTINE_END = datetime(2026, 4, 14, 17, 5, 54, tzinfo=timezone.utc)
CHECK_INTERVAL = 10  # seconds

print(f"Monitoring COMP M5 quarantine lift at {QUARANTINE_END.isoformat()}")
print(f"Current time: {datetime.now(timezone.utc).isoformat()}")
print(f"Waiting... (checking every {CHECK_INTERVAL}s)")
print()

while True:
    now = datetime.now(timezone.utc)
    if now >= QUARANTINE_END:
        print(f"\n[{now.strftime('%H:%M:%S')}] Quarantine window opened!")

        # Give watchdog 30 seconds to restart
        print("  Waiting 30s for watchdog restart...")
        time.sleep(30)

        # Check crypto watchdog report
        try:
            r = json.load(open('reports/watchdog/crypto_watchdog_report.json'))
            for row in r.get('rows', []):
                if 'compusd' in row.get('name', '').lower():
                    status = row.get('status')
                    pids = row.get('process_ids', [])
                    print(f"  COMP M5: status={status}, pids={pids}")
                    if status == 'ok' and pids:
                        print(f"  ✅ COMP M5 restarted successfully! PID {pids[0]}")
                    elif status == 'ok' and not pids:
                        print(f"  ⚠️ COMP M5 status ok but no process found")
                    else:
                        print(f"  ❌ COMP M5 still has issues: {status}")
                    break
            else:
                print("  ❌ COMP M5 not found in watchdog report")
        except Exception as e:
            print(f"  ❌ Error reading watchdog report: {e}")

        # Check if process is running
        import psutil
        found = False
        for p in psutil.process_iter(['pid', 'cmdline']):
            cmd = ' '.join(p.info['cmdline'] or [])
            if 'compusd' in cmd.lower() or 'comp-usd' in cmd.lower():
                print(f"  ✅ COMP M5 process found: PID {p.info['pid']}")
                found = True
                break
        if not found:
            print("  ❌ No COMP M5 process found")

        break
    else:
        remaining = (QUARANTINE_END - now).total_seconds()
        print(f"\r[{now.strftime('%H:%M:%S')}] {remaining:.0f}s until quarantine lift...", end='', flush=True)
        time.sleep(CHECK_INTERVAL)
