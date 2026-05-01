#!/usr/bin/env python3
"""Check live lane $/hour."""
import json
from pathlib import Path
from datetime import datetime, timezone

lanes = [
    ("live_btcusd_m15_warp", "reports/penetration_lattice_live_btcusd_m15_warp_state.json"),
    ("live_rearm_941777", "reports/penetration_lattice_live_rearm_941777_state.json"),
    ("live_momentum_alpha50_941778", "reports/penetration_lattice_live_momentum_alpha50_941778_state.json"),
    ("live_btcusd_exc2_tight_941779", "reports/penetration_lattice_live_btcusd_exc2_tight_941779_state.json"),
    ("shadow_btcusd_m15_step15", "reports/penetration_lattice_shadow_btcusd_m15_step15_state.json"),
    ("shadow_btcusd_m15_step20", "reports/penetration_lattice_shadow_btcusd_m15_step20_state.json"),
]

for name, path in lanes:
    sp = Path(path)
    if not sp.exists():
        print(f"{name}: no state file")
        continue
    st = json.loads(sp.read_text())
    syms = st.get("symbols", {})
    btc = list(syms.values())[0] if syms else {}
    runner = st.get("runner", {})
    closes = btc.get("realized_closes", 0)
    net = btc.get("realized_net_usd", 0)
    started = runner.get("started_at", "")
    hb = runner.get("heartbeat_at", "")
    step = st.get("metadata", {}).get("step", "?")
    
    hours = 0
    if started:
        try:
            start_dt = datetime.fromisoformat(started)
            now = datetime.now(tz=timezone.utc)
            hours = max(0.001, (now - start_dt).total_seconds() / 3600)
        except:
            pass
    
    per_hr = net / hours if hours > 0 else 0
    per_close = net / closes if closes > 0 else 0
    
    print(f"{name}: step={step}, {closes}c, net=${net:.2f}, hrs={hours:.2f}, ${per_hr:.2f}/hr, ${per_close:.2f}/close, hb={hb}")
