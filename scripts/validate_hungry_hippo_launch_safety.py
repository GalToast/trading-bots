#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hungry_hippo_symbol_profiles import infer_asset_class, runtime_defaults_for_symbol


ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT / "configs"
REPORTS_DIR = ROOT / "reports"
DEPLOYMENT_GATE_PATH = REPORTS_DIR / "hungry_hippo_deployment_safety_gate_board.json"
OUTPUT_JSON_PATH = REPORTS_DIR / "hungry_hippo_launch_safety_validation.json"
OUTPUT_MD_PATH = REPORTS_DIR / "hungry_hippo_launch_safety_validation.md"

CONFIG_GLOBS = (
    "hungry_hippo_*_live.json",
    "hungry_hippo_*_shadow.json",
    "hungry_hippo_*_deploy.json",
)
CRYPTO_TICK_UNSUPPORTED_FLAGS = {"--escape-cut-count", "--escape-max-cut-loss"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def arg_has_flag(restart_args: list[Any], flag: str) -> bool:
    return any(str(item or "").strip() == flag for item in restart_args)


def arg_value(restart_args: list[Any], flag: str) -> str:
    try:
        idx = restart_args.index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(restart_args):
        return ""
    return str(restart_args[idx + 1] or "").strip()


def parse_float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_bool(text: Any) -> bool:
    return bool(text)


def iter_config_paths(config_dir: Path = CONFIGS_DIR) -> list[Path]:
    paths: list[Path] = []
    for pattern in CONFIG_GLOBS:
        paths.extend(config_dir.glob(pattern))
    return sorted(set(path.resolve() for path in paths))


def config_scope(path: Path) -> str:
    if path.name.endswith("_live.json"):
        return "live_surface"
    if path.name.endswith("_deploy.json"):
        return "deploy_candidate"
    return "shadow_candidate"


def display_path(path: Path) -> str:
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def runner_family(script_path: str) -> str:
    script_name = Path(script_path).name
    if script_name == "live_penetration_lattice_tick_crypto_shadow.py":
        return "tick_crypto_shadow"
    if script_name == "live_penetration_lattice_tick_shadow.py":
        return "tick_shadow"
    if script_name == "live_penetration_lattice_shadow.py":
        return "legacy_bar_shadow"
    return "unknown"


def fx_step_floor(symbol: str) -> float:
    return 0.03 if symbol.upper().endswith("JPY") else 0.0003


def crypto_step_floor(symbol: str) -> float:
    base_step = float(runtime_defaults_for_symbol(symbol).get("base_step", 5.0))
    if base_step < 1.0:
        return round(base_step * 0.5, 6)
    return base_step


def load_deployment_gate_rows(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("rows") or []):
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            rows[symbol] = row
    return rows


def format_reason(reason: str) -> str:
    return reason.replace("_", " ")


def evaluate_config(path: Path, payload: dict[str, Any], gate_row: dict[str, Any] | None) -> dict[str, Any]:
    restart_args = list(payload.get("restart_args") or [])
    scope = config_scope(path)
    geometry = dict(payload.get("geometry") or {})
    close = dict(payload.get("close") or {})
    risk = dict(payload.get("risk") or {})
    script_path = str(restart_args[0] or "").strip() if restart_args else ""
    family = "config_surface" if scope == "live_surface" and not restart_args else runner_family(script_path)
    symbol = str(arg_value(restart_args, "--symbol") or payload.get("symbol") or "").upper()
    timeframe = str(arg_value(restart_args, "--timeframe") or payload.get("timeframe") or "")
    kind = str(payload.get("kind") or "")
    asset_class = infer_asset_class(symbol, kind)
    enabled = parse_bool(payload.get("enabled"))
    alpha = parse_float(arg_value(restart_args, "--raw-close-alpha"))
    if alpha is None:
        alpha = parse_float(close.get("alpha"))
    max_floating_loss = parse_float(arg_value(restart_args, "--max-floating-loss-usd"))
    if max_floating_loss is None:
        max_floating_loss = parse_float(risk.get("max_floating_loss_usd"))
    step = parse_float(arg_value(restart_args, "--step"))
    if step is None:
        step = parse_float(geometry.get("step"))
    step_buy = parse_float(arg_value(restart_args, "--step-buy"))
    if step_buy is None:
        step_buy = parse_float(geometry.get("step_buy"))
    step_sell = parse_float(arg_value(restart_args, "--step-sell"))
    if step_sell is None:
        step_sell = parse_float(geometry.get("step_sell"))
    step_candidates = [value for value in (step, step_buy, step_sell) if isinstance(value, float) and value > 0]
    min_step = min(step_candidates) if step_candidates else None
    enforce_launch_contract = scope != "live_surface"

    hard_fail_reasons: list[str] = []
    advisory_reasons: list[str] = []

    if enforce_launch_contract and not restart_args:
        hard_fail_reasons.append("missing_restart_args")
    if enforce_launch_contract and not script_path:
        hard_fail_reasons.append("missing_launcher_script")
    if not symbol:
        hard_fail_reasons.append("missing_symbol")
    if not timeframe:
        hard_fail_reasons.append("missing_timeframe")
    if alpha is None:
        hard_fail_reasons.append("missing_raw_close_alpha")
    elif enforce_launch_contract and alpha < 0.3:
        hard_fail_reasons.append("alpha_below_floor")
    elif not enforce_launch_contract and alpha < 0.3:
        advisory_reasons.append("profile_alpha_below_launch_floor")
    if max_floating_loss is None:
        hard_fail_reasons.append("missing_max_floating_loss_usd")
    elif enforce_launch_contract and max_floating_loss != -15.0:
        hard_fail_reasons.append("floating_loss_cap_not_minus_15")
    elif not enforce_launch_contract and max_floating_loss != -15.0:
        advisory_reasons.append("profile_floating_loss_cap_differs_from_launch_floor")

    if family in {"tick_crypto_shadow", "tick_shadow"}:
        if not arg_has_flag(restart_args, "--escape-hatch"):
            hard_fail_reasons.append("missing_escape_hatch_flag")
        if parse_float(arg_value(restart_args, "--escape-max-bars")) is None:
            hard_fail_reasons.append("missing_escape_max_bars")
        if parse_float(arg_value(restart_args, "--escape-max-loss")) is None:
            hard_fail_reasons.append("missing_escape_max_loss")
    elif family == "config_surface":
        advisory_reasons.append("live_surface_not_launch_contract")
    elif family == "legacy_bar_shadow":
        hard_fail_reasons.append("legacy_bar_runner_not_current_escape_contract")
    else:
        advisory_reasons.append("unknown_runner_family")

    if family == "tick_crypto_shadow":
        bad_flags = sorted(flag for flag in CRYPTO_TICK_UNSUPPORTED_FLAGS if arg_has_flag(restart_args, flag))
        if bad_flags:
            hard_fail_reasons.append("crypto_runner_has_fx_only_escape_flags")

    if min_step is None:
        hard_fail_reasons.append("missing_step_geometry")
    elif enforce_launch_contract and asset_class == "crypto" and min_step < crypto_step_floor(symbol):
        hard_fail_reasons.append("crypto_step_below_5_floor")
    elif enforce_launch_contract and asset_class == "fx" and min_step < fx_step_floor(symbol):
        hard_fail_reasons.append("fx_step_below_floor")
    elif not enforce_launch_contract and asset_class == "crypto" and min_step < crypto_step_floor(symbol):
        advisory_reasons.append("profile_crypto_step_below_launch_floor")
    elif not enforce_launch_contract and asset_class == "fx" and min_step < fx_step_floor(symbol):
        advisory_reasons.append("profile_fx_step_below_launch_floor")

    gate_verdict = str((gate_row or {}).get("deployment_verdict") or "unknown")
    gate_effective_spread = str((gate_row or {}).get("effective_spread_status") or "")
    gate_ratio = parse_float(str((gate_row or {}).get("ratio_to_atr") or ""))
    gate_proof_closes = int((gate_row or {}).get("proof_closes") or 0)
    if asset_class in {"index", "commodity"} and gate_ratio is not None and gate_ratio > 0 and gate_ratio < 0.5:
        if gate_proof_closes < 20:
            hard_fail_reasons.append("atr_micro_step_without_forward_proof")
        else:
            advisory_reasons.append("atr_micro_step_with_forward_proof")

    if gate_verdict == "hard_block":
        if gate_effective_spread == "CONTROL-UNDER-TEST":
            advisory_reasons.append("gate_hard_block_but_current_control_is_shadow_only")
        else:
            advisory_reasons.append("symbol_hard_blocked_by_live_gate")
    elif gate_verdict == "manual_review":
        advisory_reasons.append("symbol_requires_manual_review")

    validation_status = str(((payload.get("hungry_hippo_metadata") or {}).get("validation_status") or "")).lower()
    if "superseded" in validation_status:
        advisory_reasons.append("validation_status_superseded")
    if "shadow_probe_only" in validation_status or "shadow_rebuild_only" in validation_status:
        advisory_reasons.append("shadow_only_evidence_surface")

    if hard_fail_reasons:
        verdict = "fail"
    elif advisory_reasons:
        verdict = "research_only"
    else:
        verdict = "pass"

    return {
        "config_path": display_path(path),
        "name": str(payload.get("name") or path.stem),
        "scope": scope,
        "enabled": enabled,
        "symbol": symbol,
        "timeframe": timeframe,
        "asset_class": asset_class,
        "runner_family": family,
        "verdict": verdict,
        "alpha": alpha,
        "max_floating_loss_usd": max_floating_loss,
        "min_step": round(min_step, 6) if min_step is not None else None,
        "gate_verdict": gate_verdict,
        "hard_fail_reasons": hard_fail_reasons,
        "advisory_reasons": advisory_reasons,
    }


def build_payload(
    config_payloads: list[tuple[Path, dict[str, Any]]],
    deployment_gate_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate_rows = load_deployment_gate_rows(deployment_gate_payload)
    rows = [
        evaluate_config(path, payload, gate_rows.get(str(arg_value(list(payload.get("restart_args") or []), "--symbol") or "").upper()))
        for path, payload in config_payloads
    ]
    severity_order = {"fail": 0, "research_only": 1, "pass": 2}
    rows.sort(key=lambda row: (severity_order.get(str(row["verdict"]), 9), str(row["config_path"])))

    verdict_counts: dict[str, int] = {}
    launch_contract_verdict_counts: dict[str, int] = {}
    blocking_enabled = 0
    live_surface_count = 0
    launch_contract_count = 0
    for row in rows:
        verdict = str(row["verdict"])
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        if str(row.get("scope")) == "live_surface":
            live_surface_count += 1
        else:
            launch_contract_count += 1
            launch_contract_verdict_counts[verdict] = launch_contract_verdict_counts.get(verdict, 0) + 1
        if row["enabled"] and verdict == "fail":
            blocking_enabled += 1

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            *(display_path(path) for path, _ in config_payloads),
            str(DEPLOYMENT_GATE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The new safety rules only matter if they can fail a config before launch, not just explain a loss afterward.",
            "Config truth should hard-fail on direct contract violations and stay advisory on symbol-level context that may belong to a different geometry.",
            "Shadow evidence may remain worth collecting even when the same symbol is not honest to promote or deploy."
        ],
        "summary": {
            "config_count": len(rows),
            "verdict_counts": verdict_counts,
            "live_surface_count": live_surface_count,
            "launch_contract_count": launch_contract_count,
            "launch_contract_verdict_counts": launch_contract_verdict_counts,
            "blocking_enabled_config_count": blocking_enabled,
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Hungry Hippo Launch Safety Validation",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: strict preflight for Hungry Hippo shadow/deploy configs against the current launch-safety contract.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Config count: `{summary.get('config_count', 0)}`")
    lines.append(f"- Verdict counts: `{summary.get('verdict_counts', {})}`")
    lines.append(f"- Live profile surfaces: `{summary.get('live_surface_count', 0)}`")
    lines.append(f"- Launch contracts: `{summary.get('launch_contract_count', 0)}`")
    lines.append(f"- Launch-contract verdict counts: `{summary.get('launch_contract_verdict_counts', {})}`")
    lines.append(f"- Blocking enabled configs: `{summary.get('blocking_enabled_config_count', 0)}`")

    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Config | Verdict | Enabled | Symbol | Runner | Alpha | Min Step | Gate | Hard Fails | Advisory |",
            "|---|---|---:|---|---|---:|---:|---|---|---|",
        ]
    )
    for row in list(payload.get("rows") or []):
        hard_fails = ", ".join(format_reason(reason) for reason in list(row.get("hard_fail_reasons") or [])) or "none"
        advisory = ", ".join(format_reason(reason) for reason in list(row.get("advisory_reasons") or [])) or "none"
        alpha = row.get("alpha")
        min_step = row.get("min_step")
        lines.append(
            f"| {row['config_path']} | `{row['verdict']}` | {str(bool(row['enabled']))} | {row['symbol']} | "
            f"`{row['runner_family']}` | {alpha if alpha is not None else 'n/a'} | "
            f"{min_step if min_step is not None else 'n/a'} | `{row['gate_verdict']}` | {hard_fails} | {advisory} |"
        )

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def validate_configs() -> dict[str, Any]:
    config_payloads = [(path, load_json(path)) for path in iter_config_paths()]
    deployment_gate_payload = load_optional_json(DEPLOYMENT_GATE_PATH)
    return build_payload(config_payloads, deployment_gate_payload)


def main() -> int:
    payload = validate_configs()
    write_outputs(payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0 if int((payload.get("summary") or {}).get("blocking_enabled_config_count") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
