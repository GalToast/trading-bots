#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
JSON_PATH = REPORTS / "live_crypto_trigger_proximity_board.json"
MD_PATH = REPORTS / "live_crypto_trigger_proximity_board.md"
LIVE_LANE_DASHBOARD_JSON = REPORTS / "live_lane_dashboard.json"


@dataclass(frozen=True)
class CryptoProbeContract:
    lane: str
    symbol: str
    state_path: Path
    event_path: Path


CRYPTO_PROBES: list[CryptoProbeContract] = [
    CryptoProbeContract(
        lane="live_ethusd_m5_warp_5_941890",
        symbol="ETHUSD",
        state_path=REPORTS / "penetration_lattice_live_ethusd_m5_warp_5_state.json",
        event_path=REPORTS / "penetration_lattice_live_ethusd_m5_warp_5_events.jsonl",
    ),
    CryptoProbeContract(
        lane="live_solusd_m15_warp_v2_941891",
        symbol="SOLUSD",
        state_path=REPORTS / "penetration_lattice_live_solusd_m15_warp_v2_state.json",
        event_path=REPORTS / "penetration_lattice_live_solusd_m15_warp_v2_events.jsonl",
    ),
    CryptoProbeContract(
        lane="live_xrpusd_m15_hh_breakout_941892",
        symbol="XRPUSD",
        state_path=REPORTS / "penetration_lattice_live_xrpusd_m15_hh_breakout_state.json",
        event_path=REPORTS / "penetration_lattice_live_xrpusd_m15_hh_breakout_events.jsonl",
    ),
    CryptoProbeContract(
        lane="live_adausd_m15_warp_941893",
        symbol="ADAUSD",
        state_path=REPORTS / "penetration_lattice_live_adausd_m15_warp_state.json",
        event_path=REPORTS / "penetration_lattice_live_adausd_m15_warp_events.jsonl",
    ),
    CryptoProbeContract(
        lane="live_ltcusd_m15_warp_941894",
        symbol="LTCUSD",
        state_path=REPORTS / "penetration_lattice_live_ltcusd_m15_warp_state.json",
        event_path=REPORTS / "penetration_lattice_live_ltcusd_m15_warp_events.jsonl",
    ),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_last_quote(event_path: Path) -> dict[str, Any]:
    if not event_path.exists():
        return {}
    try:
        lines = event_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {}
    for raw_line in reversed(lines[-500:]):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        bid = parse_float(payload.get("bid"))
        ask = parse_float(payload.get("ask"))
        if bid is None or ask is None:
            continue
        return {
            "bid": bid,
            "ask": ask,
            "action": str(payload.get("action") or ""),
            "ts_utc": str(payload.get("ts_utc") or ""),
        }
    return {}


def classify_distance_status(*, buy_gap_steps: float, sell_gap_steps: float) -> tuple[str, str]:
    if buy_gap_steps <= 0.0:
        return "buy_crossed_now", "BUY"
    if sell_gap_steps <= 0.0:
        return "sell_crossed_now", "SELL"
    nearest_side = "BUY" if buy_gap_steps <= sell_gap_steps else "SELL"
    nearest_steps = min(buy_gap_steps, sell_gap_steps)
    if nearest_steps <= 0.75:
        return "within_three_quarters_step", nearest_side
    if nearest_steps <= 1.5:
        return "within_one_and_half_steps", nearest_side
    return "multi_step_idle", nearest_side


def classify_execution_read(
    *,
    close_count: int,
    open_count: int,
    spread_gate_status: str,
    distance_status: str,
) -> str:
    if close_count > 0 or open_count > 0:
        return "post_fill_or_inventory_live"
    if spread_gate_status == "blocked_now":
        return "spread_blocked_before_first_fill"
    if distance_status.endswith("_crossed_now"):
        return "crossed_waiting_first_fill"
    return "waiting_for_first_fill"


def classify_spread_gate_status(
    *,
    spread_ratio: float,
    max_entry_spread_ratio: float,
    liquidity_gap_spread_multiplier: float,
    liquidity_gap_spread_floor_ratio: float,
) -> str:
    fixed_threshold = max(0.0, float(max_entry_spread_ratio or 0.0))
    if fixed_threshold > 0.0:
        return "admissible_now" if spread_ratio <= fixed_threshold else "blocked_now"

    adaptive_multiplier = max(0.0, float(liquidity_gap_spread_multiplier or 0.0))
    adaptive_floor = max(0.0, float(liquidity_gap_spread_floor_ratio or 0.0))
    if adaptive_multiplier > 0.0:
        if spread_ratio <= adaptive_floor:
            return "admissible_now"
        return "adaptive_guard_active"

    return "blocked_now"


def compute_probe_row(
    contract: CryptoProbeContract,
    *,
    dashboard_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    state = load_json(contract.state_path)
    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    symbol_row = symbols.get(contract.symbol) if isinstance(symbols.get(contract.symbol), dict) else {}
    quote = parse_last_quote(contract.event_path)
    dashboard_row = dashboard_rows.get(contract.lane, {})

    bid = parse_float(quote.get("bid")) or 0.0
    ask = parse_float(quote.get("ask")) or 0.0
    next_buy_level = parse_float(symbol_row.get("next_buy_level")) or 0.0
    next_sell_level = parse_float(symbol_row.get("next_sell_level")) or 0.0
    base_step_px = (
        parse_float(symbol_row.get("base_step_px"))
        or parse_float(symbol_row.get("base_step_buy_px"))
        or parse_float(symbol_row.get("base_step_sell_px"))
        or parse_float((state.get("metadata") if isinstance(state.get("metadata"), dict) else {}).get("step"))
        or 0.0
    )
    max_entry_spread_ratio = parse_float(symbol_row.get("max_entry_spread_ratio")) or 0.0
    liquidity_gap_spread_multiplier = parse_float(symbol_row.get("liquidity_gap_spread_multiplier")) or 0.0
    liquidity_gap_spread_floor_ratio = parse_float(symbol_row.get("liquidity_gap_spread_floor_ratio")) or 0.0
    spread_px = max(0.0, ask - bid)
    spread_ratio = (spread_px / base_step_px) if base_step_px > 0 else 0.0
    buy_gap_px = ask - next_buy_level
    sell_gap_px = next_sell_level - bid
    buy_gap_steps = (buy_gap_px / base_step_px) if base_step_px > 0 else 0.0
    sell_gap_steps = (sell_gap_px / base_step_px) if base_step_px > 0 else 0.0
    distance_status, nearest_side = classify_distance_status(
        buy_gap_steps=buy_gap_steps,
        sell_gap_steps=sell_gap_steps,
    )
    nearest_gap_px = buy_gap_px if nearest_side == "BUY" else sell_gap_px
    nearest_gap_steps = buy_gap_steps if nearest_side == "BUY" else sell_gap_steps
    spread_gate_status = classify_spread_gate_status(
        spread_ratio=spread_ratio,
        max_entry_spread_ratio=max_entry_spread_ratio,
        liquidity_gap_spread_multiplier=liquidity_gap_spread_multiplier,
        liquidity_gap_spread_floor_ratio=liquidity_gap_spread_floor_ratio,
    )
    close_count = int(symbol_row.get("realized_closes") or 0)
    open_count = len(symbol_row.get("open_tickets") or [])
    execution_read = classify_execution_read(
        close_count=close_count,
        open_count=open_count,
        spread_gate_status=spread_gate_status,
        distance_status=distance_status,
    )

    return {
        "lane": contract.lane,
        "symbol": contract.symbol,
        "status": str(dashboard_row.get("status") or ""),
        "evidence_basis": str(dashboard_row.get("evidence_basis") or ""),
        "operator_posture": str(dashboard_row.get("operator_posture") or ""),
        "started_at": str(dashboard_row.get("started_at") or ""),
        "heartbeat_at": str(dashboard_row.get("heartbeat_at") or ""),
        "quote_ts_utc": str(quote.get("ts_utc") or ""),
        "quote_action": str(quote.get("action") or ""),
        "bid": bid,
        "ask": ask,
        "spread_px": spread_px,
        "spread_ratio": spread_ratio,
        "max_entry_spread_ratio": max_entry_spread_ratio,
        "liquidity_gap_spread_multiplier": liquidity_gap_spread_multiplier,
        "liquidity_gap_spread_floor_ratio": liquidity_gap_spread_floor_ratio,
        "spread_gate_status": spread_gate_status,
        "step_px": base_step_px,
        "next_buy_level": next_buy_level,
        "next_sell_level": next_sell_level,
        "buy_gap_px": buy_gap_px,
        "sell_gap_px": sell_gap_px,
        "buy_gap_steps": buy_gap_steps,
        "sell_gap_steps": sell_gap_steps,
        "nearest_side": nearest_side,
        "nearest_gap_px": nearest_gap_px,
        "nearest_gap_steps": nearest_gap_steps,
        "distance_status": distance_status,
        "execution_read": execution_read,
        "close_count": close_count,
        "open_count": open_count,
        "anchor_resets": int(symbol_row.get("anchor_resets") or 0),
        "realized_net_usd": float(symbol_row.get("realized_net_usd") or 0.0),
    }


def is_active_live_crypto_probe(row: dict[str, Any]) -> bool:
    evidence_basis = str(row.get("evidence_basis") or "")
    return evidence_basis != "decommissioned_or_parked"


def build_payload() -> dict[str, Any]:
    dashboard = load_json(LIVE_LANE_DASHBOARD_JSON)
    dashboard_rows_raw = dashboard.get("rows") if isinstance(dashboard.get("rows"), list) else []
    dashboard_rows = {
        str(row.get("lane") or ""): row
        for row in dashboard_rows_raw
        if isinstance(row, dict) and str(row.get("lane") or "").strip()
    }
    rows = [
        compute_probe_row(contract, dashboard_rows=dashboard_rows)
        for contract in CRYPTO_PROBES
        if is_active_live_crypto_probe(dashboard_rows.get(contract.lane, {}))
    ]
    rows.sort(key=lambda row: (row["nearest_gap_steps"], row["symbol"]))

    crossed = [row for row in rows if row["distance_status"].endswith("_crossed_now")]
    admissible = [row for row in rows if row["spread_gate_status"] in {"admissible_now", "adaptive_guard_active"}]
    within_one_step = [row for row in rows if 0.0 < row["nearest_gap_steps"] <= 1.0]
    waiting_first_fill = [row for row in rows if row["execution_read"] == "waiting_for_first_fill"]
    nearest = rows[0] if rows else {}

    current_read: list[str] = []
    current_read.append(
        f"{len(admissible)}/{len(rows)} crypto probes are currently spread-admissible; the limiting condition is trigger distance, not live admission friction."
    )
    if waiting_first_fill:
        current_read.append(
            f"{len(waiting_first_fill)}/{len(rows)} probes are in explicit waiting-for-first-fill posture: broker-flat, zero-close, and currently spread-admissible."
        )
    if nearest:
        current_read.append(
            f"Nearest step-normalized trigger is {nearest['symbol']} on the {nearest['nearest_side']} side at {nearest['nearest_gap_steps']:.3f} steps ({nearest['nearest_gap_px']:+.6f} price units)."
        )
    if within_one_step:
        names = ", ".join(
            f"{row['symbol']}({row['nearest_side']} {row['nearest_gap_steps']:.3f} step)"
            for row in within_one_step
        )
        current_read.append(f"Current sub-1-step watch set: {names}.")
    if crossed:
        names = ", ".join(f"{row['symbol']}:{row['nearest_side']}" for row in crossed)
        current_read.append(f"Executable-side quote is currently across the trigger on: {names}.")
    else:
        current_read.append(
            f"No active crypto probe is currently quote-crossed on the executable side; all {len(rows)} are honest no-trigger idle right now."
        )

    return {
        "generated_at": utc_now_iso(),
        "watch_order_by_steps": [row["symbol"] for row in rows],
        "current_read": current_read,
        "summary": {
            "probe_count": len(rows),
            "spread_admissible_count": len(admissible),
            "within_one_step_count": len(within_one_step),
            "crossed_count": len(crossed),
            "waiting_for_first_fill_count": len(waiting_first_fill),
            "nearest_symbol": nearest.get("symbol") or "",
            "nearest_side": nearest.get("nearest_side") or "",
            "nearest_gap_steps": nearest.get("nearest_gap_steps") if nearest else None,
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Live Crypto Trigger Proximity Board",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Watch order by normalized trigger distance: `{payload['watch_order_by_steps']}`",
        "",
        "## Current Read",
        "",
    ]
    for line in payload.get("current_read") or []:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Symbol | Lane | Status | Evidence | Posture | Execution Read | Bid | Ask | Spread/Step | Gate | Next Buy | Buy Gap (steps) | Next Sell | Sell Gap (steps) | Nearest | Distance Status | Closes | Resets |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |",
        ]
    )
    for row in payload.get("rows") or []:
        lines.append(
            f"| `{row['symbol']}` | `{row['lane']}` | `{row['status']}` | `{row['evidence_basis']}` | `{row['operator_posture']}` | `{row['execution_read']}` | "
            f"{row['bid']:.6f} | {row['ask']:.6f} | {row['spread_ratio']:.3f} | `{row['spread_gate_status']}` | "
            f"{row['next_buy_level']:.6f} | {row['buy_gap_steps']:+.3f} | {row['next_sell_level']:.6f} | {row['sell_gap_steps']:+.3f} | "
            f"`{row['nearest_side']}` {row['nearest_gap_steps']:+.3f} | `{row['distance_status']}` | {row['close_count']} | {row['anchor_resets']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
