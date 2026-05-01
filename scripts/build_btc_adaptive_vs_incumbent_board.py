import json
from pathlib import Path
import sys
import datetime

sys.path.insert(0, str(Path("scripts").resolve()))
from unified_objective import UnifiedObjective
from validate_unified_objective_historical import load_lane_from_state

targets = [
    ("shadow_btcusd_m15_warp_restore_v1", "BTC adaptive restore shadow", "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json", "BTCUSD"),
    ("live_btcusd_m15_warp_941781", "BTC M15 $75 incumbent", "penetration_lattice_live_btcusd_m15_warp_state.json", "BTCUSD"),
    ("shadow_btcusd_m15_warp", "$15 step shadow", "penetration_lattice_shadow_btcusd_m15_warp_state.json", "BTCUSD"),
    ("shadow_btcusd_m15_warp_on20", "$20 step shadow", "penetration_lattice_shadow_btcusd_m15_warp_on20_state.json", "BTCUSD")
]

REPORTS = Path("reports")
out_md = REPORTS / "btc_adaptive_vs_incumbent_board.md"

def parse_ts(ts_str):
    if not ts_str: return None
    try:
        return datetime.datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except:
        return None

lines = [
    "# BTC Adaptive vs Incumbent Comparison Board",
    "",
    "| Lane | Role | Unified Score | $/close | closes/hr | Reset Rate | Max Open | Floating Ratio |",
    "|------|------|---------------|---------|-----------|------------|----------|----------------|"
]

for lane_name, role, state_file, symbol in targets:
    path = REPORTS / state_file
    if not path.exists():
        lines.append(f"| `{lane_name}` | {role} | missing | - | - | - | - | - |")
        continue
    
    try:
        with open(path) as f:
            st = json.load(f)
    except Exception as e:
        lines.append(f"| `{lane_name}` | {role} | error | - | - | - | - | - |")
        continue

    sym_data = st.get("symbols", {}).get(symbol, {})
    if not sym_data:
        lines.append(f"| `{lane_name}` | {role} | no symbol data | - | - | - | - | - |")
        continue

    # Extract metrics
    realized = sym_data.get("close_count") or sym_data.get("realized_closes") or 0
    net = sym_data.get("net_realized_usd") or sym_data.get("realized_net_usd") or 0.0
    floating = sym_data.get("floating_pnl_usd") or 0.0
    opens = sym_data.get("open_count") or 0
    resets = sym_data.get("reset_count") or sym_data.get("anchor_resets") or 0
    max_open = sym_data.get("max_open_total") or opens
    
    # Calculate hours
    hours = 0.1
    if "first_path_close_time" in sym_data and sym_data["first_path_close_time"] > 0 and "last_tick_time" in sym_data:
        hours = max((sym_data["last_tick_time"] - sym_data["first_path_close_time"]) / 3600.0, 0.1)
    else:
        start = parse_ts(st.get("runner", {}).get("started_at"))
        end = parse_ts(st.get("runner", {}).get("heartbeat_at"))
        if start and end:
            hours = max((end - start).total_seconds() / 3600.0, 0.1)

    # Compute derived
    dpc = net / realized if realized > 0 else 0.0
    cph = realized / hours
    reset_rate = resets / realized if realized > 0 else 0.0
    
    denom = abs(net) + abs(floating)
    float_ratio = abs(floating) / denom if denom > 0 else 0.0

    # Get Unified Score
    inp = load_lane_from_state(state_file, symbol)
    score_str = "-"
    if inp:
        res = UnifiedObjective.evaluate(inp)
        score_str = f"{res.total:+.2f}"

    lines.append(f"| `{lane_name}` | {role} | {score_str} | ${dpc:.2f} | {cph:.2f} | {reset_rate:.2f} | {max_open} | {float_ratio:.1%} |")

out_md.write_text("\n".join(lines) + "\n")
print(f"Wrote {out_md}")
