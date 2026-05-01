#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SYNC_STATE_PATH = ROOT / "reports" / "spot_microstructure_sync_state.json"
SYNC_ANALYSIS_PATH = ROOT / "reports" / "spot_microstructure_sync_analysis.json"
SYNC_JSONL_PATH = ROOT / "reports" / "spot_microstructure_sync.jsonl"
PREDATORY_STATE_PATH = ROOT / "reports" / "predatory_shadow_monitor_state.json"
PREDATORY_EVENT_PATH = ROOT / "reports" / "predatory_shadow_monitor_events.jsonl"
PREDATORY_ALIGNMENT_PATH = ROOT / "reports" / "predatory_signal_alignment.json"
EMPIRICAL_SNAPSHOT_PATH = ROOT / "reports" / "empirical_execution_snapshot.json"
RAVE_EXECUTION_TRUTH_PATH = ROOT / "reports" / "rave_v2_execution_truth.json"
RAVE_STATE_PATH = ROOT / "reports" / "coinbase_rsi_shadow_raveusd_state.json"
STRICT_WARP_STATE_PATH = ROOT / "reports" / "lattice_warp_grinder_strict_shadow_state.json"
FORTRESS_BENCHMARK_PATH = ROOT / "reports" / "omni_vip_fortress_v4_salvage_benchmark.json"
BENCHMARK_FRAMEWORK_PATH = ROOT / "reports" / "benchmark_results.json"
BENCHMARK_HARNESS_PATH = ROOT / "reports" / "benchmark_harness_results.json"
OUT_JSON_PATH = ROOT / "reports" / "spot_microstructure_lab_dashboard.json"
OUT_MD_PATH = ROOT / "reports" / "spot_microstructure_lab_dashboard.md"


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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def runner_health(runner: dict[str, Any] | None, *, stale_after_seconds: float) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not isinstance(runner, dict):
        return {"status": "missing", "heartbeat_age_seconds": None}
    heartbeat = parse_iso(str(runner.get("heartbeat_at") or ""))
    if heartbeat is None:
        return {"status": "missing_heartbeat", "heartbeat_age_seconds": None}
    age = max(0.0, (now - heartbeat).total_seconds())
    return {
        "status": "ok" if age <= stale_after_seconds else "stale",
        "heartbeat_age_seconds": round(age, 1),
    }


def summarize_recent_events(rows: list[dict[str, Any]], *, recent_count: int = 50) -> dict[str, int]:
    recent = rows[-recent_count:]
    counts: Counter[str] = Counter()
    for row in recent:
        action = str(row.get("action") or "").strip()
        if action:
            counts[action] += 1
    return dict(sorted(counts.items()))


def alignment_match_rate(alignment: dict[str, Any], action: str) -> str:
    by_action = alignment.get("by_action") or {}
    summary = by_action.get(action) or {}
    if not summary:
        return "n/a"
    return str(summary.get("match_rate_pct", "n/a"))


def build_dashboard() -> dict[str, Any]:
    sync_state = load_json(SYNC_STATE_PATH) or {}
    sync_analysis = load_json(SYNC_ANALYSIS_PATH) or {}
    predatory_state = load_json(PREDATORY_STATE_PATH) or {}
    predatory_events = load_jsonl(PREDATORY_EVENT_PATH)
    predatory_alignment = load_json(PREDATORY_ALIGNMENT_PATH) or {}
    empirical_snapshot = load_json(EMPIRICAL_SNAPSHOT_PATH) or {}
    rave_execution_truth = load_json(RAVE_EXECUTION_TRUTH_PATH) or {}
    rave_state = load_json(RAVE_STATE_PATH) or {}
    strict_warp_state = load_json(STRICT_WARP_STATE_PATH) or {}
    fortress_benchmark = load_json(FORTRESS_BENCHMARK_PATH) or {}

    sync_runner = sync_state.get("runner") if isinstance(sync_state.get("runner"), dict) else {}
    sync_capture = sync_state.get("capture") if isinstance(sync_state.get("capture"), dict) else {}
    sync_health = runner_health(sync_runner, stale_after_seconds=max(5.0, float(sync_runner.get("interval_seconds") or 1.0) * 4.0))

    pred_runner = predatory_state.get("runner") if isinstance(predatory_state.get("runner"), dict) else {}
    pred_monitor = predatory_state.get("monitor") if isinstance(predatory_state.get("monitor"), dict) else {}
    pred_health = runner_health(pred_runner, stale_after_seconds=max(5.0, float(pred_runner.get("poll_seconds") or 2.0) * 4.0))

    rave_runner = rave_state.get("runner") if isinstance(rave_state.get("runner"), dict) else {}
    rave_engine = rave_state.get("state") if isinstance(rave_state.get("state"), dict) else {}
    rave_health = runner_health(rave_runner, stale_after_seconds=max(60.0, float(rave_runner.get("poll_seconds") or 30.0) * 4.0))

    strict_warp_runner = strict_warp_state.get("runner") if isinstance(strict_warp_state.get("runner"), dict) else {}
    strict_warp_engine = strict_warp_state.get("engine") if isinstance(strict_warp_state.get("engine"), dict) else {}
    strict_warp_health = runner_health(strict_warp_runner, stale_after_seconds=max(8.0, float(strict_warp_runner.get("poll_seconds") or 2.0) * 4.0))

    dashboard = {
        "generated_at": utc_now_iso(),
        "capture_lane": {
            "health": sync_health,
            "runner": sync_runner,
            "capture": {
                "session_sample_count": int(sync_capture.get("sample_count") or 0),
                "output_path": str(sync_capture.get("output_path") or SYNC_JSONL_PATH),
                "coinbase_products": sync_capture.get("coinbase_products") or [],
                "kraken_pairs": sync_capture.get("kraken_pairs") or [],
            },
            "analysis": sync_analysis.get("analysis") or {},
        },
        "signal_logger_lane": {
            "health": pred_health,
            "runner": pred_runner,
            "event_counts": pred_monitor.get("event_counts") or {},
            "recent_event_mix": summarize_recent_events(predatory_events),
            "kraken_state": pred_monitor.get("kraken_state") or {},
            "alignment": predatory_alignment.get("analysis") or {},
        },
        "baseline_anchor": {
            "name": "shadow_coinbase_raveusd_rsi7",
            "health": rave_health,
            "runner": rave_runner,
            "state": {
                "realized_net_usd": float(rave_engine.get("realized_net_usd") or 0.0),
                "realized_closes": int(rave_engine.get("realized_closes") or 0),
                "cash_usd": float(rave_engine.get("cash_usd") or 0.0),
                "in_position": bool(rave_engine.get("in_position") or False),
                "signals_generated": int(rave_engine.get("signals_generated") or 0),
            },
            "empirical_model": (
                (
                    empirical_snapshot.get("fill_models") or {}
                ).get("rave_live_v2_hybrid_v1")
                if isinstance(empirical_snapshot.get("fill_models"), dict)
                else {}
            ),
            "execution_truth": (
                rave_execution_truth.get("execution_truth")
                if isinstance(rave_execution_truth.get("execution_truth"), dict)
                else {}
            ),
        },
        "strict_warp_research": {
            "health": strict_warp_health if strict_warp_state else {"status": "not_started", "heartbeat_age_seconds": None},
            "runner": strict_warp_runner,
            "state": {
                "realized_net": float(strict_warp_engine.get("realized_net") or 0.0),
                "realized_closes": int(strict_warp_engine.get("realized_closes") or 0),
                "open_positions": int(strict_warp_engine.get("open_positions") or 0),
                "pending_entry_count": int(strict_warp_engine.get("pending_entry_count") or 0),
                "pending_exit_count": int(strict_warp_engine.get("pending_exit_count") or 0),
            },
        },
        "benchmark_truth": {
            "omni_v4_salvage_best": fortress_benchmark.get("best_variant") or {},
        },
    }
    return dashboard


def render_md(dashboard: dict[str, Any]) -> str:
    capture = dashboard["capture_lane"]
    signal = dashboard["signal_logger_lane"]
    baseline = dashboard["baseline_anchor"]
    warp = dashboard["strict_warp_research"]
    best = dashboard["benchmark_truth"]["omni_v4_salvage_best"]

    lines = [
        "# Spot Microstructure Lab Dashboard",
        "",
        f"Generated: `{dashboard['generated_at']}`",
        "",
        "## Collection Lanes",
        "",
        f"- Lead-Lag Capture: `{capture['health']['status']}`; session samples `{capture['capture']['session_sample_count']}`; analyzed rows `{capture['analysis'].get('sample_count', 0)}`; heartbeat age `{capture['health']['heartbeat_age_seconds']}`s",
        f"- Signal Logger: `{signal['health']['status']}`; event counts `{signal['event_counts']}`; heartbeat age `{signal['health']['heartbeat_age_seconds']}`s",
        "",
        "## Lead-Lag Snapshot",
        "",
        f"- Avg interval seconds: `{capture['analysis'].get('avg_interval_seconds', 0.0)}`",
        f"- Avg Kraken-Coinbase diff USD: `{capture['analysis'].get('avg_diff_usd', 0.0)}`",
        f"- Significant Kraken moves: `{capture['analysis'].get('significant_kraken_moves', 0)}`",
        f"- Best follow window samples: `{capture['analysis'].get('best_follow_window_samples', 0)}`",
        f"- Best follow hit rate %: `{capture['analysis'].get('best_follow_hit_rate_pct', 0.0)}`",
        "",
        "## Predatory Snapshot",
        "",
        f"- Event counts: `{signal['event_counts']}`",
        f"- Recent event mix: `{signal['recent_event_mix']}`",
        f"- Kraken last move USD: `{signal['kraken_state'].get('last_move_usd', 0.0)}`",
        f"- Aligned signal rows: `{signal['alignment'].get('aligned_event_rows', 0)}` / `{signal['alignment'].get('signal_event_rows', 0)}`",
        f"- Buy reload match rate %: `{alignment_match_rate(signal['alignment'], 'iceberg_buy_reload_detected')}`",
        f"- Sell reload match rate %: `{alignment_match_rate(signal['alignment'], 'iceberg_sell_reload_detected')}`",
        f"- Fake floor match rate %: `{alignment_match_rate(signal['alignment'], 'fake_floor_pull_detected')}`",
        "",
        "## Baseline Anchor",
        "",
        f"- Lane: `{baseline['name']}`",
        f"- Status: `{baseline['health']['status']}`",
        f"- Realized net USD: `{baseline['state']['realized_net_usd']}`",
        f"- Realized closes: `{baseline['state']['realized_closes']}`",
        f"- Signals generated: `{baseline['state']['signals_generated']}`",
        f"- Empirical fill model: `rave_live_v2_hybrid_v1` fill_prob `{((baseline.get('empirical_model') or {}).get('resolved_for_benchmark') or {}).get('fill_prob', 'n/a')}` entry_slip `{((baseline.get('empirical_model') or {}).get('resolved_for_benchmark') or {}).get('entry_slippage_bps', 'n/a')}`bps exit_slip `{((baseline.get('empirical_model') or {}).get('resolved_for_benchmark') or {}).get('exit_slippage_bps', 'n/a')}`bps",
        f"- Execution truth provenance: `{(baseline.get('execution_truth') or {}).get('provenance', 'n/a')}`; forward events `{(baseline.get('execution_truth') or {}).get('forward_event_count', 'n/a')}` / total `{(baseline.get('execution_truth') or {}).get('total_events', 'n/a')}`",
        "",
        "## Research Comparator",
        "",
        f"- Strict warp status: `{warp['health']['status']}`",
        f"- Strict warp realized net: `{warp['state']['realized_net']}`",
        f"- Strict warp closes: `{warp['state']['realized_closes']}`",
        "",
        "## Benchmark Truth",
        "",
        f"- Omni V4 salvage best gate: `{best.get('gate', '-')}`",
        f"- Omni V4 salvage best net USD: `{best.get('realized_net_usd', 0.0)}`",
        f"- Omni V4 salvage best closes: `{best.get('realized_closes', 0)}`",
    ]

    # Add benchmark framework results if available
    bench_fw = load_json(BENCHMARK_FRAMEWORK_PATH)
    if bench_fw and "results" in bench_fw:
        lines.append("")
        lines.append("## Benchmark Framework (Fixed — Execution Model Applied)")
        lines.append("")
        for r in bench_fw["results"]:
            test_id = r.get("test_id", "?")
            coin = r.get("coin", "?")
            net = r.get("net_pnl", 0)
            trades = r.get("trades", 0)
            wr = r.get("win_rate", 0)
            dd = r.get("max_drawdown", 0)
            model = r.get("execution_model", "?")
            status = r.get("status", "?")
            icon = "✅" if status == "pass" else "❌"
            lines.append(f"- {icon} {test_id} {coin} ({model}): `${net:+.2f}` | {trades}t | {wr}%WR | {dd}%DD")

    # Add benchmark harness results if available
    bench_harness = load_json(BENCHMARK_HARNESS_PATH)
    if bench_harness:
        harness_results = bench_harness.get("results", [])
        lines.append("")
        lines.append("## Benchmark Harness (Measured Slippage)")
        if bench_harness.get("fill_model_params"):
            params = bench_harness["fill_model_params"]
            lines.append(f"- Fill model: {bench_harness.get('fill_model', '?')} (entry_slip: {params.get('entry_slippage_bps', '?')}bps, exit_slip: {params.get('exit_slippage_bps', '?')}bps)")
        lines.append("")
        if isinstance(harness_results, list):
            for entry in harness_results:
                coin = entry.get("coin", "?")
                inner = entry.get("results", {})
                if isinstance(inner, dict):
                    for tier, tier_data in inner.items():
                        net = tier_data.get("net", 0)
                        trades = tier_data.get("closes", 0)
                        wr = tier_data.get("win_rate", 0)
                        dd = tier_data.get("max_dd", 0)
                        lines.append(f"- {coin} {tier}: `${net:+.2f}` | {trades}t | {wr}%WR | {dd}%DD")
        elif isinstance(harness_results, dict):
            for coin_name, coin_data in harness_results.items():
                if isinstance(coin_data, dict) and "fee_tiers" in coin_data:
                    for tier, tier_data in coin_data["fee_tiers"].items():
                        net = tier_data.get("net", 0)
                        trades = tier_data.get("closes", 0)
                        wr = tier_data.get("win_rate", 0)
                        dd = tier_data.get("max_dd", 0)
                        lines.append(f"- {coin_name} {tier}: `${net:+.2f}` | {trades}t | {wr}%WR | {dd}%DD")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    dashboard = build_dashboard()
    OUT_JSON_PATH.write_text(json.dumps(dashboard, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD_PATH.write_text(render_md(dashboard), encoding="utf-8")
    print(json.dumps({
        "capture_status": dashboard["capture_lane"]["health"]["status"],
        "signal_status": dashboard["signal_logger_lane"]["health"]["status"],
        "baseline_status": dashboard["baseline_anchor"]["health"]["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
