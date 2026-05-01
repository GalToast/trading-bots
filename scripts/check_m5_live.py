#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TRIGGER_BOARD_JSON = REPORTS / "live_crypto_trigger_proximity_board.json"

CRYPTO_LANES: list[tuple[str, str, str]] = [
    ("ETH M5 LIVE STEP5", "penetration_lattice_live_ethusd_m5_warp_5_state.json", "ETHUSD"),
    ("SOL M15 LIVE V2", "penetration_lattice_live_solusd_m15_warp_v2_state.json", "SOLUSD"),
    ("XRP M15 LIVE HH", "penetration_lattice_live_xrpusd_m15_hh_breakout_state.json", "XRPUSD"),
    ("ADA M15 LIVE", "penetration_lattice_live_adausd_m15_warp_state.json", "ADAUSD"),
    ("LTC M15 LIVE", "penetration_lattice_live_ltcusd_m15_warp_state.json", "LTCUSD"),
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def format_probe_line(name: str, state_file: str, symbol: str) -> str:
    path = REPORTS / state_file
    if not path.exists():
        return f"{name}: NOT FOUND"

    state = load_json(path)
    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    symbol_row = symbols.get(symbol) if isinstance(symbols.get(symbol), dict) else {}
    runner = state.get("runner") if isinstance(state.get("runner"), dict) else {}

    closes = int(symbol_row.get("realized_closes") or 0)
    net = float(symbol_row.get("realized_net_usd") or 0.0)
    resets = int(symbol_row.get("anchor_resets") or 0)
    timeframe = str(symbol_row.get("timeframe") or "?")
    opens = len(symbol_row.get("open_tickets") or [])
    heartbeat = str(runner.get("heartbeat_at") or "")

    return (
        f"{name}: tf={timeframe}, closes={closes}, net=${net:.2f}, "
        f"resets={resets}, open={opens}, hb={heartbeat}"
    )


def render_trigger_watch() -> list[str]:
    payload = load_json(TRIGGER_BOARD_JSON)
    if not payload:
        return ["Trigger watch: MISSING (`python scripts/build_live_crypto_trigger_proximity_board.py`)"]

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    watch_order = payload.get("watch_order_by_steps") if isinstance(payload.get("watch_order_by_steps"), list) else []

    lines = [
        "Trigger watch:",
        f"  order={watch_order}",
        (
            "  gate="
            f"{summary.get('spread_admissible_count', 0)}/{summary.get('probe_count', len(rows))} admissible, "
            f"crossed={summary.get('crossed_count', 0)}"
        ),
        f"  waiting_first_fill={summary.get('waiting_for_first_fill_count', 0)}",
    ]
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "  "
            f"{row.get('symbol')} {row.get('nearest_side')} "
            f"{float(row.get('nearest_gap_steps') or 0.0):.3f} step "
            f"gate={row.get('spread_gate_status')} "
            f"read={row.get('execution_read') or 'unknown'}"
        )
    return lines


def main() -> int:
    for name, state_file, symbol in CRYPTO_LANES:
        print(format_probe_line(name, state_file, symbol))
    for line in render_trigger_watch():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
