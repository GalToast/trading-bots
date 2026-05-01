#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

HANDOFF_PATH = REPORTS / "btc_downtrend_handoff.json"
CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m15_sell_tight_shadow.json"

OUTPUT_JSON_PATH = REPORTS / "btc_downtrend_config_reconciliation_board.json"
OUTPUT_MD_PATH = REPORTS / "btc_downtrend_config_reconciliation_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def arg_value(config: dict[str, Any], flag: str, default: str = "") -> str:
    args = list(config.get("restart_args") or [])
    if flag not in args:
        return default
    idx = args.index(flag)
    if idx + 1 >= len(args):
        return default
    return str(args[idx + 1])


def build_payload(handoff: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    proposed = dict(handoff.get("proposed_downtrend_shape") or {})
    regime_signal = dict((handoff.get("current_truth") or {}).get("regime_signal") or {})

    config_fields = {
        "timeframe": arg_value(config, "--timeframe"),
        "step_buy": float(arg_value(config, "--step-buy", "0") or 0),
        "step_sell": float(arg_value(config, "--step-sell", "0") or 0),
        "step": float(arg_value(config, "--step", "0") or 0),
        "alpha": float(arg_value(config, "--raw-close-alpha", "0") or 0),
        "rearm_variant": arg_value(config, "--raw-rearm-variant"),
        "max_open_per_side": int(arg_value(config, "--max-open-per-side", "0") or 0),
        "sell_gap": int(arg_value(config, "--raw-sell-gap", "0") or 0),
        "buy_gap": int(arg_value(config, "--raw-buy-gap", "0") or 0),
        "enabled": bool(config.get("enabled")),
    }

    handoff_fields = {
        "timeframe": str(proposed.get("timeframe") or ""),
        "step_buy": float(proposed.get("computed_buy_step") or 0),
        "step_sell": float(proposed.get("computed_sell_step") or 0),
        "alpha": float(proposed.get("alpha") or 0),
        "rearm_variant": str(proposed.get("rearm_variant") or ""),
        "max_open_per_side": int(proposed.get("max_open_per_side") or 0),
        "sell_gap": int(proposed.get("sell_gap") or 0),
        "buy_gap": int(proposed.get("buy_gap") or 0),
        "enabled": False,
    }

    comparisons = []
    for key in ["timeframe", "step_buy", "step_sell", "alpha", "rearm_variant", "max_open_per_side", "sell_gap", "buy_gap", "enabled"]:
        handoff_value = handoff_fields[key]
        config_value = config_fields[key]
        match = handoff_value == config_value
        comparisons.append(
            {
                "field": key,
                "handoff": handoff_value,
                "config": config_value,
                "match": match,
            }
        )

    mismatches = [row for row in comparisons if not row["match"]]

    recommended_canonicalization = []
    for row in mismatches:
        field = row["field"]
        if field == "enabled":
            recommended_canonicalization.append(
                {
                    "field": field,
                    "recommended_value": False,
                    "why": "The handoff explicitly keeps the candidate shadow-only until forward proof exists, so config should not self-advertise as actively enabled in governance truth.",
                }
            )
        elif field == "max_open_per_side":
            recommended_canonicalization.append(
                {
                    "field": field,
                    "recommended_value": handoff_fields[field],
                    "why": "The handoff positions this as a controlled proof candidate; `6` is the current canonical cap until forward evidence justifies wider exposure.",
                }
            )
        elif field == "rearm_variant":
            recommended_canonicalization.append(
                {
                    "field": field,
                    "recommended_value": handoff_fields[field],
                    "why": "The handoff explicitly selected `rearm_lvl2_exc1` for the downtrend candidate; `exc2` is a stronger behavioral deviation and should require a separate documented decision.",
                }
            )
        else:
            recommended_canonicalization.append(
                {
                    "field": field,
                    "recommended_value": handoff_fields[field],
                    "why": "Prefer the handoff value so the shadow config stays aligned with the current canonical downtrend design.",
                }
            )

    summary = {
        "status": "needs_reconcile" if mismatches else "aligned",
        "match_count": len(comparisons) - len(mismatches),
        "mismatch_count": len(mismatches),
        "current_action_bias": str(regime_signal.get("action_bias") or ""),
        "current_control_mode": str(regime_signal.get("control_mode") or ""),
        "config_name": str(config.get("name") or ""),
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(HANDOFF_PATH.relative_to(ROOT)),
            str(CONFIG_PATH.relative_to(ROOT)),
        ],
        "summary": summary,
        "leadership_read": [
            "The BTC downtrend candidate no longer needs to be created; it needs to be reconciled.",
            "Core geometry already matches the handoff, but runtime-significant control fields still diverge.",
            "The canonical next step is to decide whether the repo should conform the config to the handoff or explicitly ratify the current config as a deliberate override.",
        ],
        "comparisons": comparisons,
        "recommended_canonicalization": recommended_canonicalization,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# BTC Downtrend Config Reconciliation Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: compare the existing BTC sell-tight shadow config against the downtrend handoff and make the exact drift explicit before any forward-proof judgment.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Summary", ""])
    lines.append(f"- Status: `{summary.get('status', '')}`")
    lines.append(f"- Match count: `{summary.get('match_count', 0)}`")
    lines.append(f"- Mismatch count: `{summary.get('mismatch_count', 0)}`")
    lines.append(f"- Current action bias: `{summary.get('current_action_bias', '')}`")
    lines.append(f"- Current control mode: `{summary.get('current_control_mode', '')}`")
    lines.append(f"- Config name: `{summary.get('config_name', '')}`")

    lines.extend(["", "## Field Diff", ""])
    for row in list(payload.get("comparisons") or []):
        lines.append(f"- `{row['field']}`: handoff=`{row['handoff']}` config=`{row['config']}` match=`{row['match']}`")

    lines.extend(["", "## Canonicalization", ""])
    for row in list(payload.get("recommended_canonicalization") or []):
        lines.append(f"- `{row['field']}` -> `{row['recommended_value']}`: {row['why']}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(load_json(HANDOFF_PATH), load_json(CONFIG_PATH))
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
