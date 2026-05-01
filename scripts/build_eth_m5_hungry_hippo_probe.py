#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"
DOCS = ROOT / "docs"

SALVAGE_BOARD_PATH = REPORTS / "m5_warp_salvage_board.json"
SHADOW_STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_warp_5_state.json"
LIVE_STATE_PATH = REPORTS / "penetration_lattice_live_ethusd_m5_warp_state.json"
ETH_LIVE_SURFACE_PATH = CONFIGS / "hungry_hippo_ethusd_live.json"
STALE_SPEC_PATH = REPORTS / "eth_m5_warp_live_deployment_spec.md"
STALE_PLAN_PATH = DOCS / "eth_m5_warp_graduation_plan.md"

OUTPUT_CONFIG_PATH = CONFIGS / "hungry_hippo_ethusd_m5_step5_shadow.json"
OUTPUT_JSON_PATH = REPORTS / "eth_m5_hungry_hippo_probe.json"
OUTPUT_MD_PATH = REPORTS / "eth_m5_hungry_hippo_probe.md"


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
    shadow_state_payload: dict[str, Any],
    live_state_payload: dict[str, Any],
    eth_live_payload: dict[str, Any],
) -> dict[str, Any]:
    salvage = salvage_row(salvage_payload, "shadow_ethusd_m5_warp_5")
    live_failure = salvage_row(salvage_payload, "live_ethusd_m5_warp")
    shadow_symbol = dict((shadow_state_payload.get("symbols") or {}).get("ETHUSD") or {})
    live_symbol = dict((live_state_payload.get("symbols") or {}).get("ETHUSD") or {})
    shadow_meta = dict(shadow_state_payload.get("metadata") or {})
    live_regime = dict(eth_live_payload.get("regime") or {})
    live_escape = dict(eth_live_payload.get("escape_hatch") or {})

    probe_config = {
        "name": "shadow_ethusd_m5_hungry_hippo_step5_v1",
        "kind": "shadow_crypto",
        "state_path": "reports/penetration_lattice_shadow_ethusd_m5_hungry_hippo_step5_v1_state.json",
        "event_path": "reports/penetration_lattice_shadow_ethusd_m5_hungry_hippo_step5_v1_events.jsonl",
        "poll_seconds": 30,
        "stale_after_seconds": 240,
        "process_match_substrings": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "shadow_ethusd_m5_hungry_hippo_step5_v1_state.json",
        ],
        "restart_args": [
            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
            "--symbol", "ETHUSD",
            "--timeframe", "M5",
            "--step", "5",
            "--max-open-per-side", str(int(salvage.get("max_open_per_side") or 12)),
            "--raw-close-alpha", str(float(salvage.get("alpha") or 1.0)),
            "--raw-rearm-variant", str(shadow_meta.get("raw_rearm_variant") or "rearm_lvl2_exc1"),
            "--raw-rearm-cooldown-bars", str(int(shadow_meta.get("raw_rearm_cooldown_bars") or 0)),
            "--raw-sell-gap", str(int(shadow_meta.get("raw_sell_gap") or 1)),
            "--raw-buy-gap", str(int(shadow_meta.get("raw_buy_gap") or 1)),
            "--poll-seconds", "30",
            "--shared-price-max-age-ms", str(int(shadow_meta.get("shared_price_max_age_ms") or 1000)),
            "--max-floating-loss-usd", "-15.0",
            "--max-lattice-window-bars", "240",
            "--escape-hatch",
            "--escape-max-bars", str(int((live_escape.get("tier1_breakeven") or {}).get("max_bars") or 15)),
            "--escape-max-loss", str(float((live_escape.get("tier1_breakeven") or {}).get("max_loss") or 3.0)),
            "--escape-cut-count", str(int((live_escape.get("tier2_extreme") or {}).get("cut_count") or 1)),
            "--escape-max-cut-loss", str(float((live_escape.get("tier2_extreme") or {}).get("max_loss_per_position") or 5.0)),
            "--state-path", "reports/penetration_lattice_shadow_ethusd_m5_hungry_hippo_step5_v1_state.json",
            "--event-path", "reports/penetration_lattice_shadow_ethusd_m5_hungry_hippo_step5_v1_events.jsonl",
        ],
        "enabled": False,
        "watchdog_group": "crypto_watchdog",
        "hungry_hippo_metadata": {
            "personality": "NO_SESSION_GATE_HARVEST",
            "probe_source": "m5_warp_salvage_board + shadow_ethusd_m5_warp_5 state",
            "salvage_verdict": str(salvage.get("verdict") or ""),
            "baseline_realized_net_usd": float(salvage.get("realized_net_usd") or 0.0),
            "baseline_realized_closes": int(salvage.get("realized_closes") or 0),
            "baseline_avg_per_close": float(salvage.get("avg_per_close") or 0.0),
            "baseline_resets": int(salvage.get("total_resets") or 0),
            "baseline_open_positions": int(salvage.get("open_positions") or 0),
            "failed_live_reference_net_usd": float(live_failure.get("realized_net_usd") or 0.0),
            "failed_live_reference_closes": int(live_failure.get("realized_closes") or 0),
            "regime_alignment": f"{live_regime.get('control_mode', 'mixed_hold')} on ETH M15 live surface; probe stays ungated and shadow-only",
            "validation_status": "shadow_rebuild_only_old_live_promotion_memo_superseded",
            "deploy_priority": 2,
            "risk_notes": (
                "Preserves the positive ETH M5 step5 shadow geometry and no-session-gate posture while adding escape-hatch support. "
                "This explicitly replaces the stale live-promotion memo with a shadow rebuild path."
            ),
            "guardrails": {
                "kill_on_reset_storm": True,
                "max_resets_per_hour": 6,
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
            str(SHADOW_STATE_PATH.relative_to(ROOT)),
            str(LIVE_STATE_PATH.relative_to(ROOT)),
            str(ETH_LIVE_SURFACE_PATH.relative_to(ROOT)),
            str(STALE_SPEC_PATH.relative_to(ROOT)),
            str(STALE_PLAN_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The old ETH M5 live-promotion docs are now stale because the actual live lane later went negative while the shadow $5 lane stayed positive.",
            "This rebuild preserves the positive ETH M5 step5 shadow geometry, keeps the no-session-gate posture, and adds escape-hatch control instead of relaunching the old live lane unchanged.",
            "The probe stays shadow-only and disabled by default; it is a runtime handoff surface, not an implicit launch instruction.",
        ],
        "shadow_baseline": {
            "anchor": float(shadow_symbol.get("anchor") or 0.0),
            "realized_closes": int(shadow_symbol.get("realized_closes") or salvage.get("realized_closes") or 0),
            "realized_net_usd": float(shadow_symbol.get("realized_net_usd") or salvage.get("realized_net_usd") or 0.0),
            "open_tickets": len(list(shadow_symbol.get("open_tickets") or [])),
            "rearm_opens": int(shadow_symbol.get("rearm_opens") or 0),
            "anchor_resets": int(shadow_symbol.get("anchor_resets") or 0),
            "heartbeat_at": str(((shadow_state_payload.get("runner") or {}).get("heartbeat_at")) or ""),
        },
        "failed_live_reference": {
            "realized_closes": int(live_symbol.get("realized_closes") or live_failure.get("realized_closes") or 0),
            "realized_net_usd": float(live_symbol.get("realized_net_usd") or live_failure.get("realized_net_usd") or 0.0),
            "anchor_resets": int(live_symbol.get("anchor_resets") or 0),
            "max_floating_loss_usd": float((live_state_payload.get("metadata") or {}).get("max_floating_loss_usd") or 0.0),
            "heartbeat_at": str(((live_state_payload.get("runner") or {}).get("heartbeat_at")) or ""),
        },
        "probe_hypothesis": {
            "what_is_preserved": [
                "symmetric step5 geometry",
                "alpha=1.0",
                "rearm_lvl2_exc1",
                "no session gate",
            ],
            "what_is_new": [
                "explicit shadow-only hungry hippo rebuild surface",
                "escape-hatch support from the universal config stack",
                "durable note that the earlier live deployment memo is superseded by actual live loss evidence",
            ],
            "success_gate": "reconfirm positive $/close under the rebuilt shadow surface without recreating the old live lane's negative drift",
        },
        "probe_config": probe_config,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    config = dict(payload.get("probe_config") or {})
    meta = dict(config.get("hungry_hippo_metadata") or {})
    shadow = dict(payload.get("shadow_baseline") or {})
    failed = dict(payload.get("failed_live_reference") or {})
    hypothesis = dict(payload.get("probe_hypothesis") or {})
    lines = [
        "# ETH M5 Hungry Hippo Probe",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: create a corrected ETH M5 no-session-gate hungry hippo rebuild surface that supersedes the stale live-promotion memo.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Shadow Baseline",
            "",
            f"- Anchor: `{shadow.get('anchor', 0.0):.3f}`",
            f"- Realized closes: `{shadow.get('realized_closes', 0)}`",
            f"- Realized net USD: `{shadow.get('realized_net_usd', 0.0):+.2f}`",
            f"- Open tickets in saved state: `{shadow.get('open_tickets', 0)}`",
            f"- Rearm opens: `{shadow.get('rearm_opens', 0)}`",
            f"- Anchor resets: `{shadow.get('anchor_resets', 0)}`",
            f"- Baseline heartbeat: `{shadow.get('heartbeat_at', '-')}`",
            "",
            "## Failed Live Reference",
            "",
            f"- Realized closes: `{failed.get('realized_closes', 0)}`",
            f"- Realized net USD: `{failed.get('realized_net_usd', 0.0):+.2f}`",
            f"- Anchor resets: `{failed.get('anchor_resets', 0)}`",
            f"- Prior floating-loss limit: `{failed.get('max_floating_loss_usd', 0.0):+.2f}`",
            f"- Live heartbeat reference: `{failed.get('heartbeat_at', '-')}`",
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
            "- Safe next move: `have a runtime owner review and launch this as shadow-only if they accept the corrected shadow-vs-live read and the no-session-gate rebuild thesis`",
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
        load_json(SHADOW_STATE_PATH),
        load_json(LIVE_STATE_PATH),
        load_json(ETH_LIVE_SURFACE_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_CONFIG_PATH}")
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
