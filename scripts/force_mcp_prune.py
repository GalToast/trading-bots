import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import comms_server

# FORCE RESTORE
sys.stdout = sys.__stdout__
print("STDOUT RESTORED")

print("Pruning switchboard state...")
with comms_server.state_lock():
    state = comms_server.load_state()
    print(f"Messages before: {len(state['messages'])}")
    print(f"Agents before: {len(state['agents'])}")
    comms_server.write_state(state)
    
    # Reload to verify
    new_state = comms_server.load_state()
    print(f"Messages after: {len(new_state['messages'])}")
    print(f"Agents after: {len(new_state['agents'])}")
