#!/usr/bin/env python3
"""Snapshot all active tight-step lanes."""
import json
from pathlib import Path

lanes = [
    ("Live BTC $15", "reports/penetration_lattice_live_btcusd_m15_warp_state.json"),
    ("Shadow BTC $15", "reports/penetration_lattice_shadow_btcusd_m15_step15_state.json"),
    ("Shadow BTC $20", "reports/penetration_lattice_shadow_btcusd_m15_step20_state.json"),
    ("FX EURUSD", "reports/penetration_lattice_shadow_eurusd_m15_btc_tight15_state.json"),
    ("FX GBPUSD", "reports/penetration_lattice_shadow_gbpusd_m15_btc_tight15_state.json"),
    ("FX USDJPY", "reports/penetration_lattice_shadow_usdjpy_m15_btc_tight15_state.json"),
    ("FX AUDUSD", "reports/penetration_lattice_shadow_audusd_m15_btc_tight15_state.json"),
    ("FX NZDUSD", "reports/penetration_lattice_shadow_nzdusd_m15_btc_tight15_state.json"),
    ("FX USDCAD", "reports/penetration_lattice_shadow_usdcad_m15_btc_tight15_state.json"),
]

for name, path in lanes:
    sp = Path(path)
    if not sp.exists():
        print(f"{name}: no state")
        continue
    try:
        st = json.loads(sp.read_text())
        sym = list(st.get("symbols", {}).values())[0] if st.get("symbols") else {}
        closes = sym.get("realized_closes", 0)
        net = sym.get("realized_net_usd", 0)
        opens = len(sym.get("open_tickets", []))
        avg = net / closes if closes > 0 else 0
        print(f"{name}: {closes}c, ${net:.2f}, ${avg:.2f}/c, {opens} open")
    except (PermissionError, json.JSONDecodeError):
        print(f"{name}: locked")
