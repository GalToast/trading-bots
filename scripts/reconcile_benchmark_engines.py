#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark_framework import ExecutionConfig, rsi_rave_strategy
from benchmark_harness import run_benchmark as run_harness_benchmark
from benchmark_shared import BUILTIN_FILL_MODELS, FEE_TIERS, RAVE_RSI_MR_BASELINE_PARAMS, framework_execution_kwargs
from candle_cache_service import load_candles


ROOT = Path(__file__).resolve().parent.parent
OUT_JSON_PATH = ROOT / "reports" / "benchmark_engine_reconciliation.json"
OUT_MD_PATH = ROOT / "reports" / "benchmark_engine_reconciliation.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reconcile() -> dict[str, Any]:
    candles = load_candles("RAVE-USD", "FIVE_MINUTE", 7, max_age_minutes=10000)
    if not candles or len(candles) < 100:
        return {"error": "insufficient_candles"}

    results: dict[str, Any] = {}
    for model_name in ("perfect", "realistic", "harsh"):
        harness_model = dict(BUILTIN_FILL_MODELS[model_name])
        framework_execution = ExecutionConfig(name=model_name, **framework_execution_kwargs(harness_model))
        framework_result = rsi_rave_strategy(candles, fee_bps=40, execution=framework_execution)
        harness_result = run_harness_benchmark(
            candles,
            [],
            dict(RAVE_RSI_MR_BASELINE_PARAMS),
            FEE_TIERS["40bps"],
            harness_model,
            48.0,
        )
        results[model_name] = {
            "framework": framework_result,
            "harness": {
                "net_pnl": harness_result["net"],
                "return_pct": harness_result["return_pct"],
                "trades": harness_result["closes"],
                "wins": harness_result["wins"],
                "losses": harness_result["losses"],
                "win_rate": harness_result["win_rate"],
                "total_volume": harness_result["total_volume"],
                "total_fees": harness_result["total_fees"],
                "max_drawdown": harness_result["max_dd"],
            },
            "delta": {
                "net_pnl": round(framework_result["net_pnl"] - harness_result["net"], 4),
                "trades": int(framework_result["trades"] - harness_result["closes"]),
                "win_rate": round(framework_result["win_rate"] - harness_result["win_rate"], 4),
                "max_drawdown": round(framework_result["max_drawdown"] - harness_result["max_dd"], 4),
            },
        }

    return {
        "generated_at": utc_now_iso(),
        "symbol": "RAVE-USD",
        "window_days": 7,
        "granularity": "FIVE_MINUTE",
        "strategy_params": dict(RAVE_RSI_MR_BASELINE_PARAMS),
        "fee_tier": "40bps",
        "candle_count": len(candles),
        "models": results,
    }


def render_md(payload: dict[str, Any]) -> str:
    if "error" in payload:
        return f"# Benchmark Engine Reconciliation\n\n- Error: `{payload['error']}`\n"
    lines = [
        "# Benchmark Engine Reconciliation",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"- Symbol: `{payload['symbol']}`",
        f"- Window days: `{payload['window_days']}`",
        f"- Granularity: `{payload['granularity']}`",
        f"- Candle count: `{payload['candle_count']}`",
        "",
        "| Model | Framework Net | Harness Net | Net Delta | Framework Trades | Harness Trades | WR Delta | DD Delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model_name, result in payload["models"].items():
        lines.append(
            f"| {model_name} | {result['framework']['net_pnl']} | {result['harness']['net_pnl']} | {result['delta']['net_pnl']} | "
            f"{result['framework']['trades']} | {result['harness']['trades']} | {result['delta']['win_rate']} | {result['delta']['max_drawdown']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = reconcile()
    OUT_JSON_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD_PATH.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps(payload.get("models", {}), sort_keys=True) if "error" not in payload else json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
