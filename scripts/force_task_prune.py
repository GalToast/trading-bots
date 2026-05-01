import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import comms_server

# Restore stdout
sys.stdout = sys.__stdout__
print("Task Store Pruning Audit...")

task_state = comms_server.load_task_state()
print(f"Tasks before: {len(task_state.get('tasks', []))}")
print(f"Task events before: {len(task_state.get('task_events', []))}")

# Writing it back triggers the coercion and pruning we just added
comms_server.write_task_state(task_state)

new_task_state = comms_server.load_task_state()
print(f"Tasks after: {len(new_task_state.get('tasks', []))}")
print(f"Task events after: {len(new_task_state.get('task_events', []))}")
