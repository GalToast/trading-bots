#!/usr/bin/env python3
"""Build a canonical builder-facing regime signal from existing live regime feeds."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGIME_PATH = ROOT / "reports" / "regime_classification_live.json"
MTF_PATH = ROOT / "reports" / "mtf_regime_detection.json"
POLICY_SEED_PATH = ROOT / "configs" / "hungry_hippo_policy_seed_overrides.json"
OUTPUT_JSON = ROOT / "reports" / "regime_signal.json"
OUTPUT_MD = ROOT / "reports" / "regime_signal.md"


REGIME_MAP = {
    "STRONG_TREND": "trending",
    "WEAK_TREND": "trending",
    "TRANSITION": "mixed",
    "RANGE": "ranging",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_coarse_regime(value: str) -> str:
    return REGIME_MAP.get(str(value or "").upper(), "mixed")


def coarse_bias(direction: float) -> str:
    if direction >= 0.15:
        return "BUY"
    if direction <= -0.15:
        return "SELL"
    return "NEUTRAL"


def classify_consensus(coarse_bias_value: str, mtf_bias: str) -> str:
    if mtf_bias == "NEUTRAL":
        return "mtf_neutral"
    if coarse_bias_value == "NEUTRAL":
        return "low_coarse_bias"
    if coarse_bias_value == mtf_bias:
        return "aligned"
    return "conflicted"


def derive_control_mode(
    *,
    reversal_signal: str,
    mtf_regime: str,
    mtf_bias: str,
    consensus: str,
    normalized_regime: str,
) -> str:
    signal = str(reversal_signal or "").upper()
    mtf_regime = str(mtf_regime or "").upper()
    if signal.startswith("BREAKOUT_"):
        return "breakout_follow"
    if signal.startswith("BOUNCE_"):
        return "bounce_reversal"
    if "EXTREME" in mtf_regime and signal == "WAITING":
        return "wait_extreme_confirmation"
    if consensus == "aligned" and mtf_bias in {"BUY", "SELL"} and normalized_regime == "trending":
        return "trend_follow"
    if normalized_regime == "ranging":
        return "range_harvest"
    if normalized_regime == "mixed":
        return "transition_wait"
    return "mixed_hold"


def derive_action_bias(control_mode: str, mtf_bias: str) -> str:
    if control_mode in {"breakout_follow", "trend_follow"}:
        return mtf_bias if mtf_bias in {"BUY", "SELL"} else "NEUTRAL"
    if control_mode == "bounce_reversal":
        return mtf_bias if mtf_bias in {"BUY", "SELL"} else "NEUTRAL"
    return "NEUTRAL"


def build_rows(regime_payload: dict[str, Any], mtf_payload: dict[str, Any]) -> list[dict[str, Any]]:
    regime_rows = {str(row.get("symbol") or "").upper(): row for row in regime_payload.get("symbols", [])}
    symbols = sorted(set(regime_rows) & {str(symbol).upper() for symbol in mtf_payload})
    rows: list[dict[str, Any]] = []

    for symbol in symbols:
        regime_row = regime_rows[symbol]
        mtf_row = dict(mtf_payload.get(symbol) or {})
        mtf = dict(mtf_row.get("mtf") or {})
        geometry = dict(mtf_row.get("recommended_geometry") or {})

        coarse_regime_value = str(regime_row.get("regime") or "")
        normalized_regime = normalize_coarse_regime(coarse_regime_value)
        directional_bias = float(regime_row.get("directional_bias") or 0.0)
        coarse_bias_value = coarse_bias(directional_bias)
        mtf_bias = str(geometry.get("bias") or "NEUTRAL").upper()
        reversal_signal = str(mtf.get("reversal_signal") or "NONE")
        consensus = classify_consensus(coarse_bias_value, mtf_bias)
        control_mode = derive_control_mode(
            reversal_signal=reversal_signal,
            mtf_regime=str(mtf.get("regime") or ""),
            mtf_bias=mtf_bias,
            consensus=consensus,
            normalized_regime=normalized_regime,
        )
        action_bias = derive_action_bias(control_mode, mtf_bias)

        rows.append(
            {
                "symbol": symbol,
                "coarse_regime": coarse_regime_value,
                "normalized_regime": normalized_regime,
                "coarse_bias": coarse_bias_value,
                "directional_bias": directional_bias,
                "step_coeff": float(regime_row.get("step_coeff") or 0.0),
                "mtf_regime": str(mtf.get("regime") or ""),
                "reversal_signal": reversal_signal,
                "mtf_bias": mtf_bias,
                "consensus": consensus,
                "control_mode": control_mode,
                "action_bias": action_bias,
                "confluence": int(mtf.get("confluence") or 0),
                "buy_step_coeff": float(geometry.get("buy_step_coeff") or 0.0),
                "sell_step_coeff": float(geometry.get("sell_step_coeff") or 0.0),
                "alpha": float(geometry.get("alpha") or 0.0),
                "computed_buy_step": float(geometry.get("computed_buy_step") or 0.0),
                "computed_sell_step": float(geometry.get("computed_sell_step") or 0.0),
                "source_detected_at": str(mtf_row.get("detected_at") or regime_payload.get("generated_at") or ""),
                "why": (
                    f"coarse={coarse_regime_value}/{coarse_bias_value} vs "
                    f"mtf={mtf.get('regime')}/{reversal_signal}/{mtf_bias}"
                ),
            }
        )
    return rows


def load_policy_seed_rows(path: Path = POLICY_SEED_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = load_json(path)
    return [dict(row) for row in list(payload.get("regime_signal_rows") or []) if isinstance(row, dict)]


def merge_policy_seed_rows(rows: list[dict[str, Any]], seed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {str(row.get("symbol") or "").upper(): dict(row) for row in rows if str(row.get("symbol") or "")}
    for row in seed_rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in merged:
            merged[symbol] = dict(row)
    return [merged[symbol] for symbol in sorted(merged)]


def build_payload() -> dict[str, Any]:
    regime_payload = load_json(REGIME_PATH)
    mtf_payload = load_json(MTF_PATH)
    rows = merge_policy_seed_rows(build_rows(regime_payload, mtf_payload), load_policy_seed_rows())

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {
            "regime_classification_live": str(REGIME_PATH.relative_to(ROOT)),
            "mtf_regime_detection": str(MTF_PATH.relative_to(ROOT)),
            "policy_seed_overrides": str(POLICY_SEED_PATH.relative_to(ROOT)),
        },
        "summary": {
            "symbol_count": len(rows),
            "control_mode_counts": {
                mode: sum(1 for row in rows if row["control_mode"] == mode)
                for mode in sorted({row["control_mode"] for row in rows})
            },
            "consensus_counts": {
                mode: sum(1 for row in rows if row["consensus"] == mode)
                for mode in sorted({row["consensus"] for row in rows})
            },
        },
        "rows": rows,
        "notes": [
            "This is a synthesis surface, not a third classifier. It reconciles the existing coarse regime feed with the MTF bounce/breakout detector.",
            "Builders should consume this artifact when they need one control read per symbol instead of juggling two partially overlapping regime surfaces.",
        ],
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Regime Signal",
        "",
        "This is the canonical builder-facing regime signal, synthesized from the coarse live regime feed and the MTF bounce/breakout detector.",
        "",
        "## Current Read",
        "",
        f"- symbols: `{payload['summary']['symbol_count']}`",
        f"- control modes: `{payload['summary']['control_mode_counts']}`",
        f"- consensus: `{payload['summary']['consensus_counts']}`",
        "",
        "## Rows",
        "",
        "| Symbol | Coarse | MTF | Reversal | Consensus | Control Mode | Action | Geometry | Why |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for row in payload["rows"]:
        geometry = (
            f"buy={row['buy_step_coeff']} sell={row['sell_step_coeff']} alpha={row['alpha']}"
            if row["buy_step_coeff"] or row["sell_step_coeff"]
            else "-"
        )
        lines.append(
            f"| {row['symbol']} | {row['coarse_regime']} ({row['coarse_bias']}) | {row['mtf_regime']} ({row['mtf_bias']}) | "
            f"{row['reversal_signal']} | {row['consensus']} | {row['control_mode']} | {row['action_bias']} | {geometry} | {row['why']} |"
        )

    lines.extend(["", "## Notes", ""])
    for item in payload["notes"]:
        lines.append(f"- {item}")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_outputs() -> None:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a canonical regime signal from regime_classification_live and mtf_regime_detection."
    )
    parser.add_argument("--watch", action="store_true", help="Rebuild continuously.")
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Watch interval in seconds.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.watch:
        write_outputs()
        return 0

    interval = max(1.0, float(args.interval_seconds))
    while True:
        write_outputs()
        time.sleep(interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
