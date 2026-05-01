#!/usr/bin/env python3
"""Audit shapeshifter and personality-selector surfaces against current guardrails."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT / "configs"
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
REARM_PARAMS_PATH = ROOT / "reports" / "hungry_hippo_rearm_params.json"
PERSONALITY_SELECTOR_PATH = ROOT / "reports" / "hungry_hippo_personality_selector.json"
SHAPESHIFTER_PATH = ROOT / "configs" / "hungry_hippo_shapeshifter.json"
OUTPUT_JSON = ROOT / "reports" / "hungry_hippo_shapeshifter_guardrail_audit.json"
OUTPUT_MD = ROOT / "reports" / "hungry_hippo_shapeshifter_guardrail_audit.md"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_symbol_rows(payload: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in list(payload.get(key) or []):
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            out[symbol] = row
    return out


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def arg_value(args: list[Any], flag: str) -> str:
    for idx, item in enumerate(args):
        if str(item) == flag and idx + 1 < len(args):
            return str(args[idx + 1])
    return ""


def classify_config_kind(path: Path) -> str:
    name = path.name.lower()
    if "_deploy" in name:
        return "deploy"
    if "_live" in name:
        return "live"
    if "_shadow" in name:
        return "shadow"
    return "other"


def step_mode_from_steps(step_sell: Any, step_buy: Any) -> str:
    try:
        step_sell_value = float(step_sell or 0.0)
        step_buy_value = float(step_buy or 0.0)
    except (TypeError, ValueError):
        return ""
    if step_sell_value <= 0.0 or step_buy_value <= 0.0:
        return ""
    if step_sell_value < step_buy_value:
        return "sell_tight"
    if step_buy_value < step_sell_value:
        return "buy_tight"
    return "symmetric"


def extract_reference_config(path: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(payload.get("symbol") or "").upper()
    restart_args = list(payload.get("restart_args") or [])
    if not symbol:
        symbol = arg_value(restart_args, "--symbol").upper()
    if not symbol:
        return None

    close_alpha = None
    step_sell = None
    step_buy = None
    if restart_args:
        close_alpha = arg_value(restart_args, "--raw-close-alpha") or None
        step_sell = arg_value(restart_args, "--step-sell") or arg_value(restart_args, "--step") or None
        step_buy = arg_value(restart_args, "--step-buy") or arg_value(restart_args, "--step") or None
    else:
        close_alpha = (payload.get("close") or {}).get("alpha")
        geometry = payload.get("geometry") or {}
        step_sell = geometry.get("step_sell") or geometry.get("step")
        step_buy = geometry.get("step_buy") or geometry.get("step")

    return {
        "symbol": symbol,
        "path": display_path(path),
        "kind": classify_config_kind(path),
        "close_alpha": str(close_alpha) if close_alpha not in (None, "") else "",
        "step_sell": step_sell,
        "step_buy": step_buy,
        "step_mode": step_mode_from_steps(step_sell, step_buy),
    }


def discover_reference_configs(config_dir: Path = CONFIGS_DIR) -> dict[str, dict[str, Any]]:
    priority = {"deploy": 0, "live": 1, "shadow": 2, "other": 3}
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(config_dir.glob("hungry_hippo_*.json")):
        if path.name == SHAPESHIFTER_PATH.name:
            continue
        try:
            payload = load_json(path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        row = extract_reference_config(path, payload)
        if not row:
            continue
        symbol = str(row["symbol"])
        current = rows.get(symbol)
        if current is None or priority.get(str(row["kind"]), 9) < priority.get(str(current.get("kind")), 9):
            rows[symbol] = row
    return rows


def evaluate_row(
    symbol: str,
    selector_row: dict[str, Any],
    shapeshifter_row: dict[str, Any] | None,
    regime_row: dict[str, Any] | None,
    rearm_row: dict[str, Any] | None,
    reference_config: dict[str, Any] | None,
) -> dict[str, Any]:
    notes: list[str] = []
    status = "promotable_now"

    selector_control = str(selector_row.get("control_mode") or "")
    selector_hold_gate = bool(selector_row.get("hold_gate"))
    selector_personality = str(selector_row.get("personality") or "")

    regime_control = str((regime_row or {}).get("control_mode") or "")
    rearm_guardrail = str((rearm_row or {}).get("canonical_guardrail_status") or "uncovered")
    auto_rearm_allowed = bool((rearm_row or {}).get("auto_rearm_allowed"))

    if regime_row is None:
        status = "uncovered"
        notes.append("No canonical regime row exists for this symbol.")

    if rearm_guardrail == "blocked":
        status = "blocked_by_guardrail"
        notes.append("Current rearm guardrail blocks auto-rearm for this symbol.")
    elif rearm_guardrail == "uncovered" and status != "blocked_by_guardrail":
        status = "uncovered"
        notes.append("Current rearm surface has no canonical guardrail coverage for this symbol.")

    if regime_row and selector_control != regime_control:
        if status == "promotable_now":
            status = "research_only"
        notes.append(f"Personality selector control `{selector_control}` disagrees with regime signal `{regime_control}`.")

    if selector_hold_gate and status == "promotable_now":
        status = "research_only"
        notes.append("Selector marks a hold gate on this symbol.")

    shapeshifter_personality = ""
    shapeshifter_deployable = None
    shapeshifter_step_mode = ""
    if shapeshifter_row:
        shapeshifter_personality = str(shapeshifter_row.get("personality_name") or shapeshifter_row.get("personality") or "")
        shapeshifter_deployable = bool(shapeshifter_row.get("deployable"))
        shapeshifter_step_mode = str(shapeshifter_row.get("step_mode") or "")

    reference_path = ""
    reference_kind = ""
    if reference_config:
        reference_path = str(reference_config.get("path") or "")
        reference_kind = str(reference_config.get("kind") or "")
        reference_step_mode = str(reference_config.get("step_mode") or "")
        reference_alpha = str(reference_config.get("close_alpha") or "")
        selector_step_mode = step_mode_from_steps(selector_row.get("step_sell"), selector_row.get("step_buy"))
        shapeshifter_row_mode = step_mode_from_steps(
            (shapeshifter_row or {}).get("step_sell"),
            (shapeshifter_row or {}).get("step_buy"),
        )
        if reference_step_mode and (
            selector_step_mode and selector_step_mode != reference_step_mode
            or shapeshifter_row_mode and shapeshifter_row_mode != reference_step_mode
        ):
            if reference_kind == "deploy":
                status = "contradiction"
            notes.append(
                f"{symbol} {reference_kind} config is `{reference_step_mode}`, but selector/shapeshifter geometry points elsewhere."
            )

        if reference_alpha and shapeshifter_row and str(shapeshifter_row.get("close_alpha")) != reference_alpha:
            if reference_kind == "deploy":
                if status == "promotable_now":
                    status = "research_only"
            notes.append(
                f"{symbol} {reference_kind} config uses alpha `{reference_alpha}`, while shapeshifter proposes `{shapeshifter_row.get('close_alpha')}`."
            )

        notes.append(
            f"{symbol} reference config `{reference_path}` ({reference_kind}) uses step_sell/step_buy "
            f"`{reference_config.get('step_sell')}` / `{reference_config.get('step_buy')}`; selector uses "
            f"`{selector_row.get('step_sell')}` / `{selector_row.get('step_buy')}`."
        )

    if status == "promotable_now" and not auto_rearm_allowed:
        status = "research_only"
        notes.append("Symbol is aligned but current guardrail state does not permit auto-rearm.")

    if not notes:
        notes.append("Current selector, shapeshifter, regime, and rearm surfaces are mutually compatible.")

    return {
        "symbol": symbol,
        "selector_control_mode": selector_control,
        "regime_control_mode": regime_control,
        "selector_personality": selector_personality,
        "shapeshifter_personality": shapeshifter_personality,
        "shapeshifter_step_mode": shapeshifter_step_mode,
        "shapeshifter_deployable": shapeshifter_deployable,
        "selector_hold_gate": selector_hold_gate,
        "rearm_guardrail_status": rearm_guardrail,
        "auto_rearm_allowed": auto_rearm_allowed,
        "reference_config_path": reference_path,
        "reference_config_kind": reference_kind,
        "status": status,
        "notes": notes,
    }


def build_payload() -> dict[str, Any]:
    regime_payload = load_json(REGIME_SIGNAL_PATH)
    rearm_payload = load_json(REARM_PARAMS_PATH)
    selector_payload = load_json(PERSONALITY_SELECTOR_PATH)
    shapeshifter_payload = load_json(SHAPESHIFTER_PATH)
    reference_configs = discover_reference_configs()

    regime_rows = normalize_symbol_rows(regime_payload, "rows")
    selector_rows = dict((selector_payload.get("symbol_configs") or {}))
    shapeshifter_rows = normalize_symbol_rows(shapeshifter_payload, "symbols")
    rearm_rows = dict(rearm_payload.get("current_state_rearm_params") or {})

    symbols = sorted(set(selector_rows) | set(shapeshifter_rows))
    rows = [
        evaluate_row(
            symbol=symbol,
            selector_row=dict(selector_rows.get(symbol) or {}),
            shapeshifter_row=shapeshifter_rows.get(symbol),
            regime_row=regime_rows.get(symbol),
            rearm_row=dict(rearm_rows.get(symbol) or {}),
            reference_config=reference_configs.get(symbol),
        )
        for symbol in symbols
    ]

    status_counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in sorted({row["status"] for row in rows})
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {
            "personality_selector": str(PERSONALITY_SELECTOR_PATH.relative_to(ROOT)),
            "shapeshifter": str(SHAPESHIFTER_PATH.relative_to(ROOT)),
            "regime_signal": str(REGIME_SIGNAL_PATH.relative_to(ROOT)),
            "rearm_params": str(REARM_PARAMS_PATH.relative_to(ROOT)),
            "reference_configs_dir": str(CONFIGS_DIR.relative_to(ROOT)),
        },
        "summary": {
            "symbol_count": len(rows),
            "status_counts": status_counts,
            "promotable_symbols": [row["symbol"] for row in rows if row["status"] == "promotable_now"],
        },
        "rows": rows,
        "notes": [
            "This audit is a governance surface only. It does not change launch configs or live runners.",
            "Promotable means compatible with current regime and rearm guardrail truth, not automatically proven profitable.",
        ],
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Hungry Hippo Shapeshifter Guardrail Audit",
        "",
        "This surface checks whether the new shapeshifter body matches current regime and rearm guardrail truth.",
        "",
        "## Current Read",
        "",
        f"- symbols: `{payload['summary']['symbol_count']}`",
        f"- status counts: `{payload['summary']['status_counts']}`",
        f"- promotable now: `{payload['summary']['promotable_symbols']}`",
        "",
        "## Rows",
        "",
        "| Symbol | Selector | Regime | Rearm | Auto Rearm | Status | Key Note |",
        "|---|---|---|---|---|---|---|",
    ]

    for row in payload["rows"]:
        lines.append(
            f"| {row['symbol']} | {row['selector_personality']} / {row['selector_control_mode']} | "
            f"{row['regime_control_mode'] or 'uncovered'} | `{row['rearm_guardrail_status']}` | "
            f"{row['auto_rearm_allowed']} | `{row['status']}` | {row['notes'][0]} |"
        )

    lines.extend(["", "## Notes", ""])
    for note in payload["notes"]:
        lines.append(f"- {note}")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
