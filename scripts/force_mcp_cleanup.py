import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import switchboard_server_cleanup as sc
import comms_server

sys.stdout = sys.__stdout__
print("Switchboard Server Cleanup Audit...")

script_path = ROOT / "comms_server.py"
processes = sc.list_server_processes(script_path)
snapshot = sc.snapshot_server_processes(processes)

print(f"Found {len(snapshot)} comms_server.py processes.")
for s in snapshot:
    print(f"  PID: {s['pid']}, Parent: {s['ppid']} (Alive: {s['parent_alive']})")

plan = sc.build_startup_cleanup_plan(processes, current_pid=os.getpid(), script_path=script_path)
targets = plan.get("targets", [])

if targets:
    print(f"Terminating {len(targets)} redundant processes: {targets}")
    sc.terminate_processes(targets)
else:
    print("No redundant processes found.")
