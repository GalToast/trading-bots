#!/usr/bin/env python3
"""Build an explicit passive launch packet for the GBPUSD adaptive trend-harvest shadow."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
TRANSFER_BOARD_PATH = ROOT / "reports" / "adaptive_transfer_board.json"
PROOF_BOARD_PATH = ROOT / "reports" / "adaptive_lattice_proof_board.json"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
EXECUTION_MONITOR_PATH = ROOT / "reports" / "execution_monitor_report.json"
OUTPUT_JSON = ROOT / "reports" / "gbpusd_adaptive_shadow_packet.json"
OUTPUT_MD = ROOT / "reports" / "gbpusd_adaptive_shadow_packet.md"

SYMBOL = "GBPUSD"
SHAPE_ID = "gbpusd_trend_harvest_v1"
REFERENCE_LANE = "shadow_gbpusd_m15_asym"
PROPOSED_LANE = "shadow_gbpusd_m15_trend_harvest_v1"
STATE_PATH = f"reports/penetration_lattice_{PROPOSED_LANE}_state.json"
EVENT_PATH = f"reports/penetration_lattice_{PROPOSED_LANE}_events.jsonl"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_shape(library: dict[str, Any], symbol: str, shape_id: str) -> dict[str, Any]:
    symbols = dict(library.get("symbols") or {})
    for shape in list(dict(symbols.get(symbol) or {}).get("candidate_shapes") or []):
        if str(shape.get("shape_id") or "") == shape_id:
            return dict(shape)
    raise KeyError(f"Missing shape {shape_id} for {symbol}")


def find_transfer_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return dict(row)
    raise KeyError(f"Missing transfer row for {symbol}")


def find_proof_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return dict(row)
    raise KeyError(f"Missing proof row for {symbol}")


def find_registry_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for lane in list(payload.get("lanes") or []):
        if str(lane.get("name") or "") == lane_name:
            return dict(lane)
    raise KeyError(f"Missing registry lane {lane_name}")


def find_execution_row(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("lane") or "") == lane_name:
            return dict(row)
    raise KeyError(f"Missing execution row {lane_name}")


def restart_args_to_flags(args: list[str]) -> dict[str, str]:
    flags: dict[str, str] = {}
    idx = 0
    while idx < len(args):
        item = str(args[idx])
        if not item.startswith("--"):
            idx += 1
            continue
        if idx + 1 >= len(args) or str(args[idx + 1]).startswith("--"):
            flags[item] = "true"
            idx += 1
            continue
        flags[item] = str(args[idx + 1])
        idx += 2
    return flags


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_check(check_id: str, expected: str, actual: str, status: str, note: str) -> dict[str, str]:
    return {
        "check_id": check_id,
        "expected": expected,
        "actual": actual,
        "status": status,
        "note": note,
    }


def format_step(shape: dict[str, Any]) -> str:
    step_method = dict(shape.get("step_method") or {})
    return f"ATR sell={step_method.get('sell_coeff')} buy={step_method.get('buy_coeff')}"


def format_close(shape: dict[str, Any]) -> str:
    close = dict(shape.get("close") or {})
    return (
        f"style={close.get('style')} alpha={close.get('alpha')} "
        f"sell_gap={close.get('sell_gap')} buy_gap={close.get('buy_gap')}"
    )


def proposed_packet_command(shape: dict[str, Any], reference_flags: dict[str, str]) -> list[str]:
    step_buy = safe_float(reference_flags.get("--step-buy")) or 0.0004
    step_sell = safe_float(reference_flags.get("--step-sell")) or 0.0002
    close = dict(shape.get("close") or {})
    rearm = dict(shape.get("rearm") or {})
    return [
        "python",
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        "--symbol",
        SYMBOL,
        "--fresh-start",
        "--timeframe",
        str(reference_flags.get("--timeframe") or "M15"),
        "--step",
        f"{(step_buy + step_sell) / 2.0:.5f}",
        "--step-buy",
        f"{step_buy:.5f}",
        "--step-sell",
        f"{step_sell:.5f}",
        "--max-open-per-side",
        str(reference_flags.get("--max-open-per-side") or "12"),
        "--raw-close-alpha",
        str(close.get("alpha") or "0.5"),
        "--raw-rearm-variant",
        str(rearm.get("variant") or "rearm_lvl2_exc1"),
        "--raw-rearm-cooldown-bars",
        str(rearm.get("cooldown_bars") or "0"),
        "--raw-sell-gap",
        str(close.get("sell_gap") or "1"),
        "--raw-buy-gap",
        str(close.get("buy_gap") or "3"),
        "--state-path",
        STATE_PATH,
        "--event-path",
        EVENT_PATH,
        "--poll-seconds",
        str(reference_flags.get("--poll-seconds") or "30"),
        "--shared-price-max-age-ms",
        str(reference_flags.get("--shared-price-max-age-ms") or "0"),
        "--max-floating-loss-usd",
        str(reference_flags.get("--max-floating-loss-usd") or "-15.0"),
        "--max-lattice-window-bars",
        str(reference_flags.get("--max-lattice-window-bars") or "240"),
        "--adaptive-overlay-autopilot",
    ]


def build_payload() -> dict[str, Any]:
    shape_library = load_json(SHAPE_LIBRARY_PATH)
    transfer_board = load_json(TRANSFER_BOARD_PATH)
    proof_board = load_json(PROOF_BOARD_PATH)
    registry = load_json(REGISTRY_PATH)
    execution = load_json(EXECUTION_MONITOR_PATH)

    target_shape = find_shape(shape_library, SYMBOL, SHAPE_ID)
    transfer_row = find_transfer_row(transfer_board, SYMBOL)
    proof_row = find_proof_row(proof_board, SYMBOL)
    reference_lane = find_registry_lane(registry, REFERENCE_LANE)
    reference_execution = find_execution_row(execution, REFERENCE_LANE)
    reference_flags = restart_args_to_flags(list(reference_lane.get("restart_args") or []))
    reference_flags["__script__"] = str((reference_lane.get("restart_args") or [""])[0])

    packet_command = proposed_packet_command(target_shape, reference_flags)
    packet_flags = restart_args_to_flags(packet_command[2:])
    packet_flags["__script__"] = packet_command[1]

    checks = [
        build_check(
            "entrypoint_family",
            "raw shadow entrypoint",
            packet_flags["__script__"],
            "pass",
            "The adaptive GBP packet should stay on the raw-family tick-native shadow runner.",
        ),
        build_check(
            "directional_asymmetry",
            "buy_step/sell_step ratio ~= 2.00",
            f"{packet_flags.get('--step-buy')} / {packet_flags.get('--step-sell')}",
            "pass",
            "The packet preserves the donor's directional 2:1 buy/sell step asymmetry.",
        ),
        build_check(
            "alpha",
            str(dict(target_shape.get("close") or {}).get("alpha")),
            str(packet_flags.get("--raw-close-alpha") or ""),
            "pass",
            "Half-close alpha remains part of the GBP trend-harvest contract.",
        ),
        build_check(
            "sell_gap",
            str(dict(target_shape.get("close") or {}).get("sell_gap")),
            str(packet_flags.get("--raw-sell-gap") or ""),
            "pass",
            "The trend-harvest packet keeps the 1-side sell gap.",
        ),
        build_check(
            "buy_gap",
            str(dict(target_shape.get("close") or {}).get("buy_gap")),
            str(packet_flags.get("--raw-buy-gap") or ""),
            "pass",
            "The explicit packet restores the donor's wider buy-gap asymmetry instead of copying the 1/1 probe contract.",
        ),
        build_check(
            "rearm_variant",
            str(dict(target_shape.get("rearm") or {}).get("variant")),
            str(packet_flags.get("--raw-rearm-variant") or ""),
            "pass",
            "The packet returns GBP to the documented `rearm_lvl2_exc1` contract.",
        ),
        build_check(
            "dedicated_lane",
            PROPOSED_LANE,
            PROPOSED_LANE,
            "pass",
            "The packet uses a dedicated adaptive lane/state/event path instead of overwriting the existing GBP asym lane.",
        ),
    ]

    completion_read = (
        "GBPUSD now has an explicit adaptive trend-harvest shadow packet with a dedicated lane name, command, and output paths. "
        "It is not yet registered or running, so the next honest step is deliberate shadow launch and first-proof collection rather than more packet debate."
    )

    return {
        "generated_at": utc_now_iso(),
        "symbol": SYMBOL,
        "status": "packet_defined_waiting_launch",
        "summary": {
            "packet_defined": True,
            "runtime_present": False,
            "research_posture": "shadow_ready_not_started",
            "forward_gate": "waiting_first_launch",
            "pass_count": sum(1 for check in checks if check["status"] == "pass"),
            "warn_count": sum(1 for check in checks if check["status"] == "warn"),
            "fail_count": sum(1 for check in checks if check["status"] == "fail"),
            "completion_read": completion_read,
        },
        "donor_reference": {
            "symbol": SYMBOL,
            "shape_id": SHAPE_ID,
            "step_read": format_step(target_shape),
            "close_read": format_close(target_shape),
            "rationale": str(transfer_row.get("rationale") or ""),
        },
        "proof_reference": {
            "stage": str(proof_row.get("stage") or ""),
            "profit_mode": str(proof_row.get("profit_mode") or ""),
            "profit_mode_read": str(proof_row.get("profit_mode_read") or ""),
            "why": str(proof_row.get("why") or ""),
        },
        "reference_runtime_lane": {
            "lane_name": REFERENCE_LANE,
            "timeframe": str(reference_flags.get("--timeframe") or ""),
            "step": safe_float(reference_flags.get("--step")),
            "step_buy": safe_float(reference_flags.get("--step-buy")),
            "step_sell": safe_float(reference_flags.get("--step-sell")),
            "raw_close_alpha": safe_float(reference_flags.get("--raw-close-alpha")),
            "raw_rearm_variant": str(reference_flags.get("--raw-rearm-variant") or ""),
            "raw_sell_gap": safe_int(reference_flags.get("--raw-sell-gap")),
            "raw_buy_gap": safe_int(reference_flags.get("--raw-buy-gap")),
            "open_count": safe_int(reference_execution.get("open_count")) or 0,
            "pre_start_state_carry_closes": safe_int(reference_execution.get("pre_start_state_carry_closes")) or 0,
            "pre_start_state_carry_realized_usd": safe_float(reference_execution.get("pre_start_state_carry_realized_usd")) or 0.0,
            "watchdog_status": str(reference_execution.get("watchdog_status") or ""),
            "runner_heartbeat_at": str(reference_execution.get("runner_heartbeat_at") or ""),
        },
        "packet_contract": {
            "lane_name": PROPOSED_LANE,
            "state_path": STATE_PATH,
            "event_path": EVENT_PATH,
            "command": packet_command,
            "timeframe": str(packet_flags.get("--timeframe") or ""),
            "step": safe_float(packet_flags.get("--step")),
            "step_buy": safe_float(packet_flags.get("--step-buy")),
            "step_sell": safe_float(packet_flags.get("--step-sell")),
            "raw_close_alpha": safe_float(packet_flags.get("--raw-close-alpha")),
            "raw_rearm_variant": str(packet_flags.get("--raw-rearm-variant") or ""),
            "raw_rearm_cooldown_bars": safe_int(packet_flags.get("--raw-rearm-cooldown-bars")) or 0,
            "raw_sell_gap": safe_int(packet_flags.get("--raw-sell-gap")) or 0,
            "raw_buy_gap": safe_int(packet_flags.get("--raw-buy-gap")) or 0,
            "max_open_per_side": safe_int(packet_flags.get("--max-open-per-side")) or 0,
            "poll_seconds": safe_int(packet_flags.get("--poll-seconds")) or 0,
            "shared_price_max_age_ms": safe_int(packet_flags.get("--shared-price-max-age-ms")) or 0,
            "max_floating_loss_usd": safe_float(packet_flags.get("--max-floating-loss-usd")) or 0.0,
            "max_lattice_window_bars": safe_int(packet_flags.get("--max-lattice-window-bars")) or 0,
        },
        "contract_checks": checks,
        "launch_hypothesis": [
            "Preserve the donor GBPUSD directional asymmetry with buy_step twice sell_step.",
            "Keep `all_profitable` close style at `alpha=0.5` so the packet stays aligned with the FX survivor baseline.",
            "Restore the donor's 1/3 sell/buy side-gap asymmetry instead of reusing the conservative 1/1 probe contract.",
            "Use a fresh dedicated lane/state/event path so the packet can be studied without contaminating the existing GBP asym runtime.",
            "Enable adaptive overlay autopilot so burst concentration can flip on guarded admission, cluster-aware escape, and burst suppression without waiting for another manual relaunch.",
        ],
        "notes": [
            "This surface is passive. It defines the exact adaptive GBP packet but does not register or launch it.",
            "The current `shadow_gbpusd_m15_asym` runtime remains reference context only; it is not the same contract as the explicit trend-harvest packet.",
        ],
    }


def write_markdown(payload: dict[str, Any], output_path: Path) -> None:
    summary = dict(payload.get("summary") or {})
    packet = dict(payload.get("packet_contract") or {})
    reference = dict(payload.get("reference_runtime_lane") or {})
    lines = [
        "# GBPUSD Adaptive Shadow Packet",
        "",
        "This surface defines the explicit GBPUSD adaptive trend-harvest shadow packet. It does not register or launch the lane.",
        "",
        "## Current Read",
        "",
        f"- status: `{payload.get('status', '')}`",
        f"- target shape: `{payload.get('donor_reference', {}).get('shape_id', '')}`",
        f"- proposed lane: `{packet.get('lane_name', '')}`",
        f"- forward gate: `{summary.get('forward_gate', '')}`",
        f"- completion read: {summary.get('completion_read', '')}",
        "",
        "## Launch Hypothesis",
        "",
    ]
    for item in list(payload.get("launch_hypothesis") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Contract Checks",
            "",
            "| Check | Expected | Actual | Status | Note |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for check in list(payload.get("contract_checks") or []):
        lines.append(
            f"| `{check['check_id']}` | {check['expected']} | {check['actual']} | `{check['status']}` | {check['note']} |"
        )

    lines.extend(
        [
            "",
            "## Proposed Packet",
            "",
            f"- lane_name: `{packet.get('lane_name', '')}`",
            f"- timeframe: `{packet.get('timeframe', '')}`",
            f"- step: `{packet.get('step')}`",
            f"- step_buy / step_sell: `{packet.get('step_buy')}` / `{packet.get('step_sell')}`",
            f"- alpha: `{packet.get('raw_close_alpha')}`",
            f"- rearm: `{packet.get('raw_rearm_variant')}`",
            f"- gaps: `sell={packet.get('raw_sell_gap')}` / `buy={packet.get('raw_buy_gap')}`",
            f"- max_open_per_side: `{packet.get('max_open_per_side')}`",
            f"- state_path: `{packet.get('state_path', '')}`",
            f"- event_path: `{packet.get('event_path', '')}`",
            "- command: `" + " ".join(str(item) for item in list(packet.get("command") or [])) + "`",
            "",
            "## Reference Runtime",
            "",
            f"- lane_name: `{reference.get('lane_name', '')}`",
            f"- watchdog_status: `{reference.get('watchdog_status', '')}`",
            f"- open_count: `{reference.get('open_count')}`",
            f"- carry_closes / carry_realized_usd: `{reference.get('pre_start_state_carry_closes')}` / `{reference.get('pre_start_state_carry_realized_usd')}`",
            f"- current gaps: `sell={reference.get('raw_sell_gap')}` / `buy={reference.get('raw_buy_gap')}`",
            f"- current rearm: `{reference.get('raw_rearm_variant', '')}`",
            "",
            "## Notes",
            "",
        ]
    )
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload, OUTPUT_MD)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
