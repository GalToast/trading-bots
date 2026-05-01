#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

CONFIG_TO_PERF_PATH = REPORTS / "config_to_performance_mapping.json"
REAL_WORLD_PATH = REPORTS / "hungry_hippo_real_world_analysis.json"
SALVAGE_PATH = REPORTS / "m5_warp_salvage_board.json"
REARM_PARAMS_PATH = REPORTS / "hungry_hippo_rearm_params.json"
PROMOTION_QUEUE_PATH = REPORTS / "hungry_hippo_promotion_queue.json"
REGIME_SIGNAL_PATH = REPORTS / "regime_signal.json"

OUTPUT_CONFIG_PATH = CONFIGS / "adaptive_controller_priors.json"
OUTPUT_MD_PATH = REPORTS / "adaptive_controller_priors.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in list(payload.get("lanes") or []):
        if isinstance(row, dict) and str(row.get("lane") or "") == lane_name:
            return row
    raise KeyError(f"lane not found: {lane_name}")


def best_lane_for_symbol(
    payload: dict[str, Any],
    symbol: str,
    *,
    min_closes: int = 1,
    lane_name_contains: str | None = None,
) -> dict[str, Any]:
    def collect(required_min_closes: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for row in list(payload.get("lanes") or []):
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "") != symbol:
                continue
            if int(row.get("realized_closes") or 0) < required_min_closes:
                continue
            if lane_name_contains and lane_name_contains not in str(row.get("lane") or ""):
                continue
            candidates.append(row)
        candidates.sort(
            key=lambda row: (
                float(row.get("avg_per_close") or 0.0),
                float(row.get("realized_net_usd") or 0.0),
                int(row.get("realized_closes") or 0),
            ),
            reverse=True,
        )
        return candidates

    candidates = collect(min_closes)
    if not candidates and min_closes > 1:
        candidates = collect(1)
    if not candidates:
        raise KeyError(f"no lane found for symbol={symbol} min_closes={min_closes} lane_name_contains={lane_name_contains}")
    return candidates[0]


def rows_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("symbol") or ""): row
        for row in list(payload.get("rows") or [])
        if isinstance(row, dict) and str(row.get("symbol") or "")
    }


def max_per_close_for_symbol(real_world: dict[str, Any], symbol: str) -> float:
    best = 0.0
    for row in list(real_world.get("winning_configs") or []):
        if str(row.get("symbol") or "") != symbol:
            continue
        best = max(best, float(row.get("per_close") or 0.0))
    return best


def build_payload(
    config_to_perf: dict[str, Any],
    real_world: dict[str, Any],
    salvage: dict[str, Any],
    rearm_params: dict[str, Any],
    promotion_queue: dict[str, Any],
    regime_signal: dict[str, Any],
) -> dict[str, Any]:
    promotion_rows = rows_by_symbol(promotion_queue)
    regime_rows = rows_by_symbol(regime_signal)
    current_rearm = dict(rearm_params.get("current_state_rearm_params") or {})

    live_btc_m15 = first_lane(config_to_perf, "live_btcusd_m15_warp")
    fx_gbp_rearm = best_lane_for_symbol(config_to_perf, "GBPUSD", min_closes=50)
    fx_eur_rearm = best_lane_for_symbol(config_to_perf, "EURUSD", min_closes=50)
    eth_m5_shadow = first_lane(config_to_perf, "shadow_ethusd_m5_warp_5")
    btc_m5_shadow = first_lane(config_to_perf, "shadow_btcusd_m5_warp_step200")
    btc_m5_live = first_lane(config_to_perf, "live_btcusd_m5_warp")
    eth_m5_live = first_lane(config_to_perf, "live_ethusd_m5_warp")

    global_policy = {
        "session_gate_policy": "weighting_or_circuit_breaker_only",
        "graduation_funnel": {
            "theory_to_shadow": "requires coherent mechanism plus either offline edge or existing adjacent-family production evidence",
            "shadow_to_live": "requires forward-positive evidence, acceptable reset behavior, and no active contradiction with controller priors or guardrail blockers",
            "live_to_scale": "requires sustained forward-positive evidence plus clean survival under real execution",
        },
        "offensive_extreme_closure": {
            "status": "research_candidate",
            "read": "Treat extreme-order closure as an offensive floating-loss dampener when inner-lattice profits can absorb the closure cheaply.",
        },
        "dual_lattice_hedge": {
            "status": "research_candidate",
            "read": "Promising but not validated; keep in shadow theory stage until a clean same-symbol dual-lattice floating offset study exists.",
        },
    }

    symbol_priors = {
        "GBPUSD": {
            "controller_role": "fx_alpha_half_survivor",
            "shape_envelope": "trend_harvest_raw_family",
            "close_alpha_prior": 0.5,
            "session_gate_bias": "not_primary",
            "guardrail_status": str((current_rearm.get("GBPUSD") or {}).get("canonical_guardrail_status") or ""),
            "auto_rearm_allowed": bool((current_rearm.get("GBPUSD") or {}).get("auto_rearm_allowed")),
            "evidence": {
                "gbp_rearm_avg_per_close": round(float(fx_gbp_rearm.get("avg_per_close") or 0.0), 4),
                "best_real_world_per_close": round(max_per_close_for_symbol(real_world, "GBPUSD"), 4),
            },
            "controller_read": "Use alpha=0.5 as the canonical FX baseline. Do not let exploratory shapeshifter work override this prior without stronger forward proof.",
        },
        "EURUSD": {
            "controller_role": "fx_alpha_half_survivor",
            "shape_envelope": "mixed_floor_raw_family",
            "close_alpha_prior": 0.5,
            "session_gate_bias": "not_primary",
            "guardrail_status": str((current_rearm.get("EURUSD") or {}).get("canonical_guardrail_status") or ""),
            "auto_rearm_allowed": bool((current_rearm.get("EURUSD") or {}).get("auto_rearm_allowed")),
            "evidence": {
                "eur_rearm_avg_per_close": round(float(fx_eur_rearm.get("avg_per_close") or 0.0), 4),
                "best_real_world_per_close": round(max_per_close_for_symbol(real_world, "EURUSD"), 4),
            },
            "controller_read": "EURUSD belongs in the same FX alpha=0.5 prior family, but remains blocked for aggressive rearm under current guardrails.",
        },
        "BTCUSD": {
            "controller_role": "crypto_split_baseline",
            "live_baseline": {
                "lane": "live_btcusd_m15_warp",
                "avg_per_close": round(float(live_btc_m15.get("avg_per_close") or 0.0), 4),
                "realized_net_usd": round(float(live_btc_m15.get("realized_net_usd") or 0.0), 2),
            },
            "m5_salvage": {
                "shadow_probe_step": 200.0,
                "shadow_probe_avg_per_close": round(float(btc_m5_shadow.get("avg_per_close") or 0.0), 4),
                "shadow_probe_realized_closes": int(btc_m5_shadow.get("realized_closes") or 0),
                "failed_live_avg_per_close": round(float(btc_m5_live.get("avg_per_close") or 0.0), 4),
            },
            "guardrail_status": str((current_rearm.get("BTCUSD") or {}).get("canonical_guardrail_status") or ""),
            "promotion_action": str((promotion_rows.get("BTCUSD") or {}).get("next_action") or ""),
            "regime_action_bias": str((regime_rows.get("BTCUSD") or {}).get("action") or ""),
            "controller_read": "Preserve BTC M15 as live baseline. Keep BTC M5 in shadow-only salvage mode until the step200 probe materially outgrows the 2-close sample and BTC hold gates lift.",
        },
        "ETHUSD": {
            "controller_role": "crypto_m5_rebuild_candidate",
            "m5_shadow_baseline": {
                "step": 5.0,
                "avg_per_close": round(float(eth_m5_shadow.get("avg_per_close") or 0.0), 4),
                "realized_net_usd": round(float(eth_m5_shadow.get("realized_net_usd") or 0.0), 2),
                "realized_closes": int(eth_m5_shadow.get("realized_closes") or 0),
            },
            "failed_live_reference": {
                "avg_per_close": round(float(eth_m5_live.get("avg_per_close") or 0.0), 4),
                "realized_net_usd": round(float(eth_m5_live.get("realized_net_usd") or 0.0), 2),
                "realized_closes": int(eth_m5_live.get("realized_closes") or 0),
            },
            "guardrail_status": str((current_rearm.get("ETHUSD") or {}).get("canonical_guardrail_status") or ""),
            "promotion_action": str((promotion_rows.get("ETHUSD") or {}).get("next_action") or ""),
            "controller_read": "Use the ETH M5 step5 shadow rebuild as the canonical M5 prior. The earlier live ETH M5 promotion thesis is superseded by actual negative live realization.",
        },
        "NAS100": {
            "controller_role": "index_asym_breakout_candidate",
            "best_real_world_per_close": round(max_per_close_for_symbol(real_world, "NAS100"), 4),
            "guardrail_status": str((current_rearm.get("NAS100") or {}).get("canonical_guardrail_status") or ""),
            "promotion_action": str((promotion_rows.get("NAS100") or {}).get("next_action") or ""),
            "controller_read": "Current winning pattern is BUY-tight asymmetry in uptrend. Keep this as a high-priority shadow/live candidate, but avoid universalizing the exact geometry to all symbols.",
        },
        "US30": {
            "controller_role": "index_asym_candidate",
            "best_real_world_per_close": round(max_per_close_for_symbol(real_world, "US30"), 4),
            "guardrail_status": str((current_rearm.get("US30") or {}).get("canonical_guardrail_status") or ""),
            "promotion_action": str((promotion_rows.get("US30") or {}).get("next_action") or ""),
            "controller_read": "US30 is an index asymmetry candidate, but still needs direct forward proof before it is treated as controller-default truth.",
        },
    }

    ranked_hypotheses = [
        {
            "priority": 1,
            "hypothesis": "FX alpha=0.5 should be the universal controller prior for FX harvest shapes",
            "status": "production_backed",
            "why": "GBPUSD and EURUSD rearm survivors materially outperform alpha=1.0 FX baselines with zero resets.",
        },
        {
            "priority": 2,
            "hypothesis": "No-session-gate M5 salvage should stay shadow-first, not live-first",
            "status": "production_backed",
            "why": "BTC and ETH M5 live failures show the old direct-live restoration path is wrong, while salvage shadows still show edge.",
        },
        {
            "priority": 3,
            "hypothesis": "Asymmetric breakout geometry is strongest on indices and should be treated as an index-family prior, not a universal one",
            "status": "forward_validating",
            "why": "NAS100 HH is producing forward-positive evidence, but controller priors should stay family-scoped until more symbols validate.",
        },
        {
            "priority": 4,
            "hypothesis": "Offensive extreme closure can reduce carry drag without default session gating",
            "status": "research_candidate",
            "why": "User thesis is coherent and aligned with current escape-hatch direction, but it still needs explicit forward proof.",
        },
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(CONFIG_TO_PERF_PATH.relative_to(ROOT)),
            str(REAL_WORLD_PATH.relative_to(ROOT)),
            str(SALVAGE_PATH.relative_to(ROOT)),
            str(REARM_PARAMS_PATH.relative_to(ROOT)),
            str(PROMOTION_QUEUE_PATH.relative_to(ROOT)),
            str(REGIME_SIGNAL_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "One adaptive controller should replace most manual tuning labor, but it still needs symbol and family priors from production truth.",
            "FX alpha=0.5, no-session-gate M5 salvage, and family-scoped asymmetry are the strongest current priors to feed into the controller stack.",
            "The correct graduation path is theory to shadow to live, with symbol guardrails deciding what can graduate when.",
        ],
        "global_policy": global_policy,
        "symbol_priors": symbol_priors,
        "ranked_hypotheses": ranked_hypotheses,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Adaptive Controller Priors",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: capture production-backed controller priors so testing, shadow graduation, and live promotion use one canonical decision surface.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    global_policy = dict(payload.get("global_policy") or {})
    lines.extend(["", "## Global Policy", ""])
    lines.append(f"- Session gate policy: `{global_policy.get('session_gate_policy', '-')}`")
    funnel = dict(global_policy.get("graduation_funnel") or {})
    lines.append(f"- Theory to shadow: `{funnel.get('theory_to_shadow', '-')}`")
    lines.append(f"- Shadow to live: `{funnel.get('shadow_to_live', '-')}`")
    lines.append(f"- Live to scale: `{funnel.get('live_to_scale', '-')}`")
    lines.append(f"- Offensive extreme closure: `{dict(global_policy.get('offensive_extreme_closure') or {}).get('status', '-')}`")
    lines.append(f"- Dual lattice hedge: `{dict(global_policy.get('dual_lattice_hedge') or {}).get('status', '-')}`")

    lines.extend(["", "## Symbol Priors", ""])
    for symbol, prior in dict(payload.get("symbol_priors") or {}).items():
        lines.append(f"### {symbol}")
        lines.append("")
        lines.append(f"- Controller role: `{prior.get('controller_role', '-')}`")
        if "close_alpha_prior" in prior:
            lines.append(f"- Close alpha prior: `{prior.get('close_alpha_prior')}`")
        if "guardrail_status" in prior:
            lines.append(f"- Guardrail status: `{prior.get('guardrail_status')}`")
        if "promotion_action" in prior:
            lines.append(f"- Promotion action: `{prior.get('promotion_action')}`")
        lines.append(f"- Read: `{prior.get('controller_read', '-')}`")
        for key, value in prior.items():
            if key in {"controller_role", "close_alpha_prior", "guardrail_status", "promotion_action", "controller_read"}:
                continue
            if isinstance(value, dict):
                rendered = ", ".join(f"{k}={v}" for k, v in value.items())
                lines.append(f"- {key}: `{rendered}`")
        lines.append("")

    lines.extend(["## Ranked Hypotheses", ""])
    for item in list(payload.get("ranked_hypotheses") or []):
        lines.append(f"{int(item['priority'])}. `{item['hypothesis']}`")
        lines.append(f"   Status: {item['status']}")
        lines.append(f"   Why: {item['why']}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(CONFIG_TO_PERF_PATH),
        load_json(REAL_WORLD_PATH),
        load_json(SALVAGE_PATH),
        load_json(REARM_PARAMS_PATH),
        load_json(PROMOTION_QUEUE_PATH),
        load_json(REGIME_SIGNAL_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_CONFIG_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
