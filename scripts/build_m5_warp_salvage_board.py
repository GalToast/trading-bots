#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

CONFIG_TO_PERF_PATH = REPORTS / "config_to_performance_mapping.json"
OUT_OF_SAMPLE_PATH = REPORTS / "hungry_hippo_out_of_sample_test.json"

OUTPUT_JSON = REPORTS / "m5_warp_salvage_board.json"
OUTPUT_MD = REPORTS / "m5_warp_salvage_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(payload.get("lanes") or []):
        if isinstance(row, dict) and str(row.get("lane") or "") == lane_name:
            return row
    raise KeyError(f"lane not found: {lane_name}")


def summarize_lane(
    lane_name: str,
    row: dict[str, Any],
    verdict: str,
    next_action: str,
    thesis: str,
) -> dict[str, Any]:
    return {
        "lane": lane_name,
        "symbol": str(row.get("symbol") or ""),
        "step": float(row.get("step") or 0.0),
        "alpha": float(row.get("alpha") or 0.0),
        "max_open_per_side": int(row.get("max_open_per_side") or 0),
        "realized_closes": int(row.get("realized_closes") or 0),
        "realized_net_usd": float(row.get("realized_net_usd") or 0.0),
        "avg_per_close": float(row.get("avg_per_close") or 0.0),
        "open_positions": int(row.get("open_positions") or 0),
        "total_resets": int(row.get("total_resets") or 0),
        "reset_rate": float(row.get("reset_rate") or 0.0),
        "verdict": verdict,
        "next_action": next_action,
        "thesis": thesis,
    }


def build_payload(config_payload: dict[str, Any], out_payload: dict[str, Any]) -> dict[str, Any]:
    live_btc_m5 = summarize_lane(
        "live_btcusd_m5_warp",
        first_lane(config_payload, "live_btcusd_m5_warp"),
        verdict="do_not_restore_as_was",
        next_action="replace_with_shadow_only_salvage_probe",
        thesis="the deployed live BTC M5 config monetized badly enough that this is not a supervision or patience problem",
    )
    live_eth_m5 = summarize_lane(
        "live_ethusd_m5_warp",
        first_lane(config_payload, "live_ethusd_m5_warp"),
        verdict="do_not_restore_as_was",
        next_action="shadow_rebuild_only",
        thesis="the live ETH M5 warp also failed as deployed despite the symbol having broader lattice edge elsewhere",
    )
    shadow_btc_step200 = summarize_lane(
        "shadow_btcusd_m5_warp_step200",
        first_lane(config_payload, "shadow_btcusd_m5_warp_step200"),
        verdict="salvage_probe_candidate",
        next_action="run_with_escape_hatch_and_harvest_controller",
        thesis="wider BTC M5 spacing showed high dollars per close with no resets, but the sample is too small for live promotion",
    )
    shadow_eth_m5_step5 = summarize_lane(
        "shadow_ethusd_m5_warp_5",
        first_lane(config_payload, "shadow_ethusd_m5_warp_5"),
        verdict="strong_salvage_candidate",
        next_action="rebuild_as_hungry_hippo_shadow_variant",
        thesis="ETH M5 keeps showing meaningful realized edge in shadow, so the live failure looks like a control-stack failure more than a geometry impossibility",
    )
    live_btc_m15 = summarize_lane(
        "live_btcusd_m15_warp",
        first_lane(config_payload, "live_btcusd_m15_warp"),
        verdict="keep_as_reference_baseline",
        next_action="preserve_while_m5_salvage_stays_shadow",
        thesis="BTC M15 remains the honest live BTC baseline and should anchor any M5 revival decision",
    )

    aggregate = dict(out_payload.get("aggregate") or {})

    universal_control = {
        "session_gate_policy": "not_primary_optimizer",
        "controller_goal": "harvest_inside_extremes_without pretending stranded carry can be eliminated",
        "core_rules": [
            "stay always-on by default; use session windows only as circuit-breakers or weighting inputs, not as the main entry gate",
            "trade aggressively inside lattice extremes with denser step families and lower alpha, but only within symbol-specific safe shape envelopes",
            "accept some stranded carry as structural, then monetize around it with escape-hatch and rearm logic instead of waiting passively",
            "separate entry hunger from survival: inside-extreme harvesting can be aggressive while stale carry exits stay surgical and cheap",
            "do not use one universal shape across all symbols; use one controller over per-symbol validated families",
        ],
    }

    ranked_next_steps = [
        {
            "priority": 1,
            "action": "launch_btc_m5_step200_hungry_hippo_shadow_probe",
            "why": "best evidence-backed BTC M5 salvage row in the repo; positive realized net, very high dollars per close, zero resets, but still small-sample",
        },
        {
            "priority": 2,
            "action": "rebuild_eth_m5_as_no_session_gate_harvest_shadow",
            "why": "shadow ETH M5 step5 is meaningfully positive while live ETH M5 was negative, which points to control-stack salvage potential",
        },
        {
            "priority": 3,
            "action": "feed_fx_alpha_half_baseline_into_universal_controller",
            "why": "production data already shows FX alpha=0.5 zero-reset edge, so the universal controller should inherit that as a baseline prior instead of re-discovering it blindly",
        },
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(CONFIG_TO_PERF_PATH.relative_to(ROOT)),
            str(OUT_OF_SAMPLE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "Crypto M5 warp is not dead as a concept, but the live deployment that was turned off should not be restored as-is.",
            "The salvageable path is a hungry-hippo-style harvest controller with escape logic and no default session gate, run in shadow first on the symbols that already showed evidence of edge.",
            "A self-optimizing lattice should reduce per-symbol sweep labor, but it still needs per-symbol shape envelopes; universal control does not mean universal geometry.",
        ],
        "aggregate_shapeshifter_validation": {
            "train_shapeshifter_total": float(aggregate.get("train_shapeshifter_total") or 0.0),
            "train_static_total": float(aggregate.get("train_static_total") or 0.0),
            "test_shapeshifter_total": float(aggregate.get("test_shapeshifter_total") or 0.0),
            "test_static_total": float(aggregate.get("test_static_total") or 0.0),
            "overall_degradation": float(aggregate.get("overall_degradation") or 0.0),
            "symbols_beating_static": int(aggregate.get("symbols_beating_static") or 0),
        },
        "lanes": [
            live_btc_m5,
            live_eth_m5,
            shadow_btc_step200,
            shadow_eth_m5_step5,
            live_btc_m15,
        ],
        "universal_control_thesis": universal_control,
        "ranked_next_steps": ranked_next_steps,
    }


def render_lane_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Lane | Step | Alpha | Closes | Net USD | $/Close | Opens | Resets | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['lane']} | {row['step']:.2f} | {row['alpha']:.2f} | {row['realized_closes']} | "
            f"{row['realized_net_usd']:+.2f} | {row['avg_per_close']:+.2f} | {row['open_positions']} | {row['total_resets']} | {row['verdict']} |"
        )
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    rows = list(payload.get("lanes") or [])
    agg = dict(payload.get("aggregate_shapeshifter_validation") or {})
    thesis = dict(payload.get("universal_control_thesis") or {})
    next_steps = list(payload.get("ranked_next_steps") or [])
    lines = [
        "# M5 Warp Salvage Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: decide whether M5 warp deserves revival, and if so under what control logic.",
        (
            "- Shapeshifter validation backdrop: "
            f"`test_shapeshifter_total={agg.get('test_shapeshifter_total', 0.0):.2f}` "
            f"`test_static_total={agg.get('test_static_total', 0.0):.2f}` "
            f"`overall_degradation={agg.get('overall_degradation', 0.0):.2f}` "
            f"`symbols_beating_static={agg.get('symbols_beating_static', 0)}`"
        ),
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Core Rows", ""])
    lines.extend(render_lane_table(rows))
    lines.append("")

    for row in rows:
        lines.append(f"### {row['lane']}")
        lines.append("")
        lines.append(f"- Verdict: `{row['verdict']}`")
        lines.append(f"- Next action: `{row['next_action']}`")
        lines.append(f"- Why: `{row['thesis']}`")
        lines.append("")

    lines.extend(["## Universal Control Thesis", ""])
    lines.append(f"- Session gate policy: `{thesis.get('session_gate_policy', '-')}`")
    lines.append(f"- Controller goal: `{thesis.get('controller_goal', '-')}`")
    for rule in list(thesis.get("core_rules") or []):
        lines.append(f"- {rule}")

    lines.extend(["", "## Ranked Next Steps", ""])
    for step in next_steps:
        lines.append(f"{int(step['priority'])}. `{step['action']}`")
        lines.append(f"   Why: {step['why']}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(load_json(CONFIG_TO_PERF_PATH), load_json(OUT_OF_SAMPLE_PATH))
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
