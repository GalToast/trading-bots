import json
with open('reports/penetration_lattice_shadow_btcusd_m15_warp_state.json') as f:
    state = json.load(f)
print(f'Script: {state["runner"]["script"]}')
print(f'Symbol: {list(state["symbols"].keys())[0]}')
print(f'Timeframe: {list(state["symbols"].values())[0].get("timeframe", "N/A")}')
print(f'Step: {list(state["symbols"].values())[0].get("base_step_px", "N/A")}')
print(f'Resets: {list(state["symbols"].values())[0].get("anchor_resets", "N/A")}')
print(f'Closes: {list(state["symbols"].values())[0].get("realized_closes", "N/A")}')
