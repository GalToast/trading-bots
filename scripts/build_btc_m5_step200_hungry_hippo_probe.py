#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

SALVAGE_BOARD_PATH = REPORTS / "m5_warp_salvage_board.json"
STEP200_STATE_PATH = REPORTS / "penetration_lattice_shadow_btcusd_m5_warp_step200_state.json"
BTC_LIVE_SURFACE_PATH = CONFIGS / "hungry_hippo_btcusd_live.json"

OUTPUT_CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m5_step200_shadow.json"
OUTPUT_JSON_PATH = REPORTS / "btc_m5_step200_hungry_hippo_probe.json"
OUTPUT_MD_PATH = REPORTS / "btc_m5_step200_hungry_hippo_probe.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def salvage_row(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(payload.get("lanes") or []):
        if isinstance(row, dict) and str(row.get("lane") or "") == lane_name:
            return row
    raise KeyError(f"salvage row not found: {lane_name}")


def build_probe_payload(
    salvage_payload: dict[str, Any],
    state_payload: dict[str, Any],
    btc_live_payload: dict[str, Any],
) -> dict[str, Any]:
    salvage = salvage_row(salvage_payload, "shadow_btcusd_m5_warp_step200")
    symbol_state = dict((state_payload.get("symbols") or {}).get("BTCUSD") or {})
    metadata = dict(state_payload.get("metadata") or {})
    live_regime = dict(btc_live_payload.get("regime") or {})
    live_escape = dict(btc_live_payload.get("escape_hatch") or {})

    probe_config = {
        "name": "shadow_btcusd_m5_hungry_hippo_step200_v1",
        "kind": "shadow_crypto",
        "state_path": "reports/penetration_lattice_shadow_btcusd_m5_hungry_hippo_step200_v1_state.json",
        "event_path": "reports/penetration_lattice_shadow_btcusd_m5_hungry_hippo_step200_v1_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "shadow_btcusd_m5_hungry_hippo_step200_v1_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "BTCUSD",
            "--timeframe", "M5",
            "--step", "200",
            "--max-open-per-side", str(int(salvage.get("max_open_per_side") or 60)),
            "--raw-close-alpha", str(float(salvage.get("alpha") or 1.0)),
            "--raw-rearm-variant", str(metadata.get("raw_rearm_variant") or "rearm_lvl2_exc1"),
            "--raw-rearm-cooldown-bars", str(int(metadata.get("raw_rearm_cooldown_bars") or 0)),
            "--raw-sell-gap", str(int(metadata.get("raw_sell_gap") or 1)),
            "--raw-buy-gap", str(int(metadata.get("raw_buy_gap") or 1)),
            "--poll-seconds", "30",
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--escape-hatch",
            "--escape-max-bars", str(int((live_escape.get("tier1_breakeven") or {}).get("max_bars") or 12)),
            "--escape-max-loss", str(float((live_escape.get("tier1_breakeven") or {}).get("max_loss") or 5.0)),
            "--escape-cut-count", str(int((live_escape.get("tier2_extreme") or {}).get("cut_count") or 2)),
            "--escape-max-cut-loss", str(float((live_escape.get("tier2_extreme") or {}).get("max_loss_per_position") or 10.0)),
            "--state-path", "reports/penetration_lattice_shadow_btcusd_m5_hungry_hippo_step200_v1_state.json",
            "--event-path", "reports/penetration_lattice_shadow_btcusd_m5_hungry_hippo_step200_v1_events.jsonl",
        ],
        "enabled": False,
        "watchdog_group": "crypto_watchdog",
        "hungry_hippo_metadata": {
            "personality": "INSIDE_EXTREME_HARVEST",
            "probe_source": "m5_warp_salvage_board + shadow_btcusd_m5_warp_step200 state",
            "salvage_verdict": str(salvage.get("verdict") or ""),
            "baseline_realized_net_usd": float(salvage.get("realized_net_usd") or 0.0),
            "baseline_realized_closes": int(salvage.get("realized_closes") or 0),
            "baseline_avg_per_close": float(salvage.get("avg_per_close") or 0.0),
            "baseline_resets": int(salvage.get("total_resets") or 0),
            "baseline_open_positions": int(salvage.get("open_positions") or 0),
            "regime_alignment": f"{live_regime.get('control_mode', 'mixed')} on BTC M15 live surface; probe stays ungated and shadow-only",
            "validation_status": "shadow_probe_only_small_sample_do_not_promote_live_yet",
            "deploy_priority": 1,
            "risk_notes": (
                "Preserves the evidence-backed symmetric BTC M5 step200 geometry while layering cheap escape logic. "
                "No session gate. This is a salvage probe, not a live promotion."
            ),
            "guardrails": {
                "kill_on_reset_storm": True,
                "max_resets_per_hour": 5,
                "floating_loss_limit_usd": -15.0,
                "session_gate": None,
                "escape_hatch_enabled": True,
            },
        },
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(SALVAGE_BOARD_PATH.relative_to(ROOT)),
            str(STEP200_STATE_PATH.relative_to(ROOT)),
            str(BTC_LIVE_SURFACE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "This probe preserves the only BTC M5 salvage row in the repo with positive realized evidence and zero resets.",
            "The probe stays shadow-only and ungated; it is meant to test inside-extreme harvesting plus escape-hatch logic, not to restore the old live BTC M5 lane.",
            "The config intentionally preserves the proven symmetric step200 geometry instead of inventing asymmetry before there is forward proof.",
        ],
        "baseline_evidence": {
            "anchor": float(symbol_state.get("anchor") or 0.0),
            "realized_closes": int(symbol_state.get("realized_closes") or salvage.get("realized_closes") or 0),
            "realized_net_usd": float(symbol_state.get("realized_net_usd") or salvage.get("realized_net_usd") or 0.0),
            "open_tickets": len(list(symbol_state.get("open_tickets") or [])),
            "rearm_tokens": len(list(symbol_state.get("rearm_tokens") or [])),
            "heartbeat_at": str(((state_payload.get("runner") or {}).get("heartbeat_at")) or ""),
        },
        "probe_hypothesis": {
            "what_is_preserved": [
                "symmetric step200 geometry",
                "alpha=1.0",
                "rearm_lvl2_exc1",
                "no session gate",
            ],
            "what_is_new": [
                "dedicated hungry hippo shadow probe surface",
                "escape-hatch support from the universal config stack",
                "explicit salvage-only metadata and risk framing",
            ],
            "success_gate": "collect materially more closes than the 2-close baseline without reset explosion or toxic one-sided carry",
        },
        "probe_config": probe_config,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    config = dict(payload.get("probe_config") or {})
    meta = dict(config.get("hungry_hippo_metadata") or {})
    evidence = dict(payload.get("baseline_evidence") or {})
    hypothesis = dict(payload.get("probe_hypothesis") or {})
    lines = [
        "# BTC M5 Step200 Hungry Hippo Probe",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: create a concrete BTC M5 step200 Hungry Hippo shadow probe surface from the salvage board without taking runtime ownership.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Baseline Evidence",
            "",
            f"- Anchor: `{evidence.get('anchor', 0.0):.2f}`",
            f"- Realized closes: `{evidence.get('realized_closes', 0)}`",
            f"- Realized net USD: `{evidence.get('realized_net_usd', 0.0):+.2f}`",
            f"- Open tickets in saved state: `{evidence.get('open_tickets', 0)}`",
            f"- Rearm tokens in saved state: `{evidence.get('rearm_tokens', 0)}`",
            f"- Baseline heartbeat: `{evidence.get('heartbeat_at', '-')}`",
            "",
            "## Probe Hypothesis",
            "",
        ]
    )
    for item in list(hypothesis.get("what_is_preserved") or []):
        lines.append(f"- Preserved: {item}")
    for item in list(hypothesis.get("what_is_new") or []):
        lines.append(f"- New: {item}")
    lines.append(f"- Success gate: `{hypothesis.get('success_gate', '-')}`")

    lines.extend(
        [
            "",
            "## Config Summary",
            "",
            f"- Config path: `{OUTPUT_CONFIG_PATH.relative_to(ROOT)}`",
            f"- Runtime kind: `{config.get('kind', '')}`",
            f"- Enabled by default: `{str(bool(config.get('enabled'))).lower()}`",
            f"- Watchdog group: `{config.get('watchdog_group', '-')}`",
            f"- Risk notes: `{meta.get('risk_notes', '-')}`",
            f"- Validation status: `{meta.get('validation_status', '-')}`",
            "",
            "## Launch Readiness",
            "",
            "- Runtime ownership: `not claimed in this slice`",
            "- Safe next move: `have a runtime owner review and launch this as shadow-only if they accept the small-sample evidence and the explicit escape-hatch overlay`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    config = dict(payload.get("probe_config") or {})
    OUTPUT_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_probe_payload(
        load_json(SALVAGE_BOARD_PATH),
        load_json(STEP200_STATE_PATH),
        load_json(BTC_LIVE_SURFACE_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_CONFIG_PATH}")
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
