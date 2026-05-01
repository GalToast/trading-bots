import json
from pathlib import Path
import sys

# Add scripts dir to path
sys.path.insert(0, str(Path("scripts").resolve()))

from unified_objective import UnifiedObjective
from validate_unified_objective_historical import load_lane_from_state

targets = [
    ("shadow_btcusd_m15_warp", "penetration_lattice_shadow_btcusd_m15_warp_state.json", "BTCUSD"),
    ("shadow_gbpusd_m15_btc_tight15", "penetration_lattice_shadow_gbpusd_m15_btc_tight15_state.json", "GBPUSD"),
    ("live_btcusd_exc2_tight_941779", "penetration_lattice_shadow_btcusd_exc2_tight_state.json", "BTCUSD")
]

print("Unified Objective Evaluation Results:\n")
for name, state_file, symbol in targets:
    inp = load_lane_from_state(state_file, symbol)
    if inp:
        res = UnifiedObjective.evaluate(inp)
        print(f"[{name}]")
        print(f"  Score:   {res.total:+.2f}")
        print(f"  Verdict: {res.verdict}")
        print(f"  Raw:     Closes={inp.close_count}, Realized=${inp.realized_net_usd:.2f}, Floating=${inp.floating_usd:.2f}, Opens={inp.open_count}, Resets={inp.anchor_reset_count}, MAE=${inp.max_adverse_excursion_usd:.2f}")
        print()
    else:
        print(f"[{name}] -> State file or symbol data missing\n")
