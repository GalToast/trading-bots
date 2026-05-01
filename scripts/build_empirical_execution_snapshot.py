#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
LIVE_V2_STATE_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_state.json"
BASELINE_STATE_PATH = ROOT / "reports" / "coinbase_rsi_shadow_raveusd_state.json"
ALIGNMENT_PATH = ROOT / "reports" / "predatory_signal_alignment.json"
LAB_DASHBOARD_PATH = ROOT / "reports" / "spot_microstructure_lab_dashboard.json"
SLIPPAGE_ANALYSIS_PATH = ROOT / "reports" / "actual_slippage_analysis.json"
EXECUTION_TRUTH_PATH = ROOT / "reports" / "rave_v2_execution_truth.json"
OUT_JSON_PATH = ROOT / "reports" / "empirical_execution_snapshot.json"
OUT_MD_PATH = ROOT / "reports" / "empirical_execution_snapshot.md"

DEFAULT_BENCHMARK_FALLBACK = {
    "entry_slippage_bps": 50.0,
    "exit_slippage_bps": 50.0,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def build_snapshot() -> dict[str, Any]:
    live_v2 = load_json(LIVE_V2_STATE_PATH) or {}
    baseline = load_json(BASELINE_STATE_PATH) or {}
    alignment = load_json(ALIGNMENT_PATH) or {}
    dashboard = load_json(LAB_DASHBOARD_PATH) or {}
    slippage = load_json(SLIPPAGE_ANALYSIS_PATH) or {}
    execution_truth_payload = load_json(EXECUTION_TRUTH_PATH) or {}

    live_state = live_v2.get("state") if isinstance(live_v2.get("state"), dict) else {}
    baseline_state = baseline.get("state") if isinstance(baseline.get("state"), dict) else {}
    alignment_analysis = alignment.get("analysis") if isinstance(alignment.get("analysis"), dict) else {}
    alignment_by_product = alignment_analysis.get("by_product_action") if isinstance(alignment_analysis.get("by_product_action"), dict) else {}
    dashboard_capture = (
        (dashboard.get("capture_lane") or {}).get("analysis")
        if isinstance((dashboard.get("capture_lane") or {}).get("analysis"), dict)
        else {}
    )
    execution_truth = (
        execution_truth_payload.get("execution_truth")
        if isinstance(execution_truth_payload.get("execution_truth"), dict)
        else {}
    )

    # Load measured slippage if available
    rt_cost = slippage.get("round_trip_cost") or {}
    measured_entry_slip = rt_cost.get("entry_slippage_bps", DEFAULT_BENCHMARK_FALLBACK["entry_slippage_bps"])
    measured_exit_slip = rt_cost.get("exit_slippage_bps", DEFAULT_BENCHMARK_FALLBACK["exit_slippage_bps"])
    has_measured_slippage = "entry_slippage_bps" in rt_cost

    live_signal_count = int(live_state.get("rsi_signals") or 0)
    live_entry_count = int(live_state.get("closes") or 0) + (1 if isinstance(live_state.get("position"), dict) and live_state.get("position") else 0)
    live_fill_prob = round(live_entry_count / live_signal_count, 4) if live_signal_count > 0 else 0.0
    provenance = str(execution_truth.get("provenance") or "unknown")
    forward_event_count = int(execution_truth.get("forward_event_count") or 0)
    total_events = int(execution_truth.get("total_events") or 0)
    execution_warning = str(execution_truth.get("warning") or "")

    if provenance == "forward_only":
        confidence = "high"
    elif provenance == "mixed":
        confidence = "medium"
    elif provenance == "startup_backfill_only":
        confidence = "low"
    else:
        confidence = "medium" if has_measured_slippage else "low"

    rave_sell = alignment_by_product.get("RAVE-USD::iceberg_sell_reload_detected") or {}
    rave_buy = alignment_by_product.get("RAVE-USD::iceberg_buy_reload_detected") or {}

    fill_models = {
        "rave_live_v2_hybrid_v1": {
            "symbol": "RAVE-USD",
            "kind": "hybrid_empirical",
            "description": "Live V2 observed signal fill rate with measured slippage from candle-close backfill." if has_measured_slippage else "Live V2 observed signal fill rate with explicit slippage fallback until measured slippage lands.",
            "confidence": confidence,
            "measured": {
                "signal_count": live_signal_count,
                "entry_count": live_entry_count,
                "fill_prob": live_fill_prob,
                "realized_closes": int(live_state.get("closes") or 0),
                "realized_net": float(live_state.get("realized_net") or 0.0),
                "win_rate_pct": float(live_state.get("win_rate") or 0.0),
                "total_volume": float(live_state.get("total_volume") or 0.0),
                "total_fees": float(live_state.get("total_fees") or 0.0),
                "updated_at": str(live_v2.get("updated_at") or ""),
            },
            "execution_truth": {
                "provenance": provenance,
                "forward_event_count": forward_event_count,
                "total_events": total_events,
                "warning": execution_warning,
                "source": "reports/rave_v2_execution_truth.json",
            },
            "measured_slippage": {
                "entry_slippage_bps": measured_entry_slip,
                "exit_slippage_bps": measured_exit_slip,
                "source": "reports/actual_slippage_analysis.json",
                "caveat": "Backfill-optimal: candle-close entries have near-zero slippage. Live forward may differ.",
            } if has_measured_slippage else None,
            "fallback_assumptions": {
                "entry_slippage_bps": DEFAULT_BENCHMARK_FALLBACK["entry_slippage_bps"],
                "exit_slippage_bps": DEFAULT_BENCHMARK_FALLBACK["exit_slippage_bps"],
                "reason": "No direct live slippage telemetry is persisted yet; using current Lane 5 realistic fallback values explicitly.",
            } if not has_measured_slippage else None,
            "resolved_for_benchmark": {
                "fill_prob": live_fill_prob,
                "entry_slippage_bps": measured_entry_slip if has_measured_slippage else DEFAULT_BENCHMARK_FALLBACK["entry_slippage_bps"],
                "exit_slippage_bps": measured_exit_slip if has_measured_slippage else DEFAULT_BENCHMARK_FALLBACK["exit_slippage_bps"],
                "note": "Measured from live V2 candle-close backfill" if has_measured_slippage else "Fallback assumption — no measured slippage",
                "execution_provenance": provenance,
                "forward_event_count": forward_event_count,
                "total_events": total_events,
                "warning": execution_warning,
            },
        }
    }

    signal_overlays = {
        "rave_iceberg_sell_reload_v1": {
            "symbol": "RAVE-USD",
            "action": "iceberg_sell_reload_detected",
            "follow_seconds": float(alignment_analysis.get("follow_seconds") or 0.0),
            "count": int(rave_sell.get("count") or 0),
            "match_rate_pct": float(rave_sell.get("match_rate_pct") or 0.0),
            "avg_delta_bps": float(rave_sell.get("avg_delta_bps") or 0.0),
        },
        "rave_iceberg_buy_reload_v1": {
            "symbol": "RAVE-USD",
            "action": "iceberg_buy_reload_detected",
            "follow_seconds": float(alignment_analysis.get("follow_seconds") or 0.0),
            "count": int(rave_buy.get("count") or 0),
            "match_rate_pct": float(rave_buy.get("match_rate_pct") or 0.0),
            "avg_delta_bps": float(rave_buy.get("avg_delta_bps") or 0.0),
        },
    }

    return {
        "generated_at": utc_now_iso(),
        "sample_window": {
            "sync_rows": int(dashboard_capture.get("sample_count") or 0),
            "significant_kraken_moves": int(dashboard_capture.get("significant_kraken_moves") or 0),
            "alignment_rows": int(alignment_analysis.get("aligned_event_rows") or 0),
            "alignment_follow_seconds": float(alignment_analysis.get("follow_seconds") or 0.0),
        },
        "sources": {
            "live_v2_state_path": str(LIVE_V2_STATE_PATH),
            "baseline_state_path": str(BASELINE_STATE_PATH),
            "alignment_path": str(ALIGNMENT_PATH),
            "lab_dashboard_path": str(LAB_DASHBOARD_PATH),
            "execution_truth_path": str(EXECUTION_TRUTH_PATH),
        },
        "control_lane": {
            "symbol": "RAVE-USD",
            "baseline_realized_closes": int(baseline_state.get("realized_closes") or 0),
            "baseline_realized_net_usd": float(baseline_state.get("realized_net_usd") or 0.0),
            "baseline_signals_generated": int(baseline_state.get("signals_generated") or 0),
            "baseline_updated_at": str(baseline.get("updated_at") or ""),
        },
        "fill_models": fill_models,
        "signal_overlays": signal_overlays,
    }


def render_md(payload: dict[str, Any]) -> str:
    model = payload["fill_models"]["rave_live_v2_hybrid_v1"]
    overlays = payload["signal_overlays"]
    lines = [
        "# Empirical Execution Snapshot",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Sample Window",
        "",
        f"- Sync rows: `{payload['sample_window']['sync_rows']}`",
        f"- Significant Kraken moves: `{payload['sample_window']['significant_kraken_moves']}`",
        f"- Alignment rows: `{payload['sample_window']['alignment_rows']}`",
        f"- Alignment follow seconds: `{payload['sample_window']['alignment_follow_seconds']}`",
        "",
        "## Fill Model",
        "",
        f"- Name: `rave_live_v2_hybrid_v1`",
        f"- Confidence: `{model['confidence']}`",
        f"- Live signal count: `{model['measured']['signal_count']}`",
        f"- Live entry count: `{model['measured']['entry_count']}`",
        f"- Measured fill probability: `{model['measured']['fill_prob']}`",
        f"- Benchmark entry slippage bps: `{model['resolved_for_benchmark']['entry_slippage_bps']}`",
        f"- Benchmark exit slippage bps: `{model['resolved_for_benchmark']['exit_slippage_bps']}`",
        f"- Execution provenance: `{model['execution_truth']['provenance']}`; forward events `{model['execution_truth']['forward_event_count']}` / total `{model['execution_truth']['total_events']}`",
        "",
        "## Signal Overlays",
        "",
        f"- `rave_iceberg_sell_reload_v1`: count `{overlays['rave_iceberg_sell_reload_v1']['count']}`, match `{overlays['rave_iceberg_sell_reload_v1']['match_rate_pct']}`%, avg `{overlays['rave_iceberg_sell_reload_v1']['avg_delta_bps']}` bps",
        f"- `rave_iceberg_buy_reload_v1`: count `{overlays['rave_iceberg_buy_reload_v1']['count']}`, match `{overlays['rave_iceberg_buy_reload_v1']['match_rate_pct']}`%, avg `{overlays['rave_iceberg_buy_reload_v1']['avg_delta_bps']}` bps",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    payload = build_snapshot()
    OUT_JSON_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD_PATH.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps(payload["fill_models"]["rave_live_v2_hybrid_v1"]["resolved_for_benchmark"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
