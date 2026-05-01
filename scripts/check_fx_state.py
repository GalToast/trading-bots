import json
from pathlib import Path

reports = Path("reports")

# Check FX Rearm
d = json.load(open(reports / "penetration_lattice_shadow_state.json"))
print("=== FX REARM ===")
print(f"Updated: {d.get('updated_at', '?')}")
r = d.get('runner', {})
print(f"Runner HB: {r.get('heartbeat_at', '?')}")
print(f"PID: {r.get('pid', '?')}")
print(f"Exceptions: {r.get('consecutive_exceptions', '?')}")
s = d['symbols'].get('EURUSD', {})
print(f"EURUSD realized: {s.get('realized_net_usd', 0):.2f} ({s.get('realized_closes', 0)}c)")
print(f"EURUSD open: {len(s.get('open_tickets', []))}")
print(f"EURUSD step: {s.get('base_step_px', '?')}")
print(f"EURUSD alpha: {s.get('raw_close_alpha', '?')}")
print()

# Check FX micro configs
for sym in ["GBPUSD", "EURUSD", "NZDUSD"]:
    fname = f"penetration_lattice_shadow_{sym.lower()}_m15_fxmicro_state.json"
    p = reports / fname
    if p.exists():
        d = json.load(open(p))
        s = d['symbols'].get(sym, {})
        r = d.get('runner', {})
        print(f"=== {sym} M15 FXMICRO ===")
        print(f"Realized: {s.get('realized_net_usd', 0):.2f} ({s.get('realized_closes', 0)}c)")
        print(f"Open: {len(s.get('open_tickets', []))}")
        print(f"Resets: {s.get('anchor_resets', 0)}")
        print(f"Step: {s.get('base_step_px', '?')}")
        print(f"Close alpha: {s.get('raw_close_alpha', '?')}")
        print(f"Close mode: {s.get('close_realism_mode', '?')}")
        print(f"Variant: {s.get('variant', '?')}")
        print(f"Gap: sell={s.get('raw_sell_gap', '?')} buy={s.get('raw_buy_gap', '?')}")
        print(f"Max open: {s.get('max_open_per_side', '?')}")
        print()
