#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

CONFIG_PATH = REPORTS / "coinbase_isolated_runner_sleeve_book_config.json"
JSON_PATH = REPORTS / "coinbase_isolated_runner_sleeve_smoke_manifest.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_sleeve_smoke_manifest.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def quote(path: Path) -> str:
    return f"\"{path}\""


def run_command(coin: str, cycles: int) -> str:
    stem = coin.lower().replace("-", "").replace("_", "")
    state_path = REPORTS / f"multi_coin_isolated_runner_sleeve_{stem}_state.json"
    event_path = REPORTS / f"multi_coin_isolated_runner_sleeve_{stem}_events.jsonl"
    return (
        f"python scripts/multi_coin_isolated_runner.py --config-path {quote(CONFIG_PATH)} --total-cash 48 "
        f"--coins {coin} --state-path {quote(state_path)} --event-path {quote(event_path)} --max-cycles {cycles}"
    )


def build_payload() -> dict[str, Any]:
    config_payload = load_json(CONFIG_PATH)
    rows = []
    for idx, row in enumerate(config_payload.get("configs", []), start=1):
        status = str(row.get("config_status") or "")
        proof_class = "exact_config_smoke" if status.startswith("exact_") else "inferred_config_smoke"
        rows.append(
            {
                "coin": row["coin"],
                "board_strategy": row["board_strategy"],
                "runner_strategy": row["strategy"],
                "config_status": status,
                "proof_class": proof_class,
                "smoke_command": run_command(row["coin"], 5),
                "supervised_command": run_command(row["coin"], 20),
                "priority_rank": idx,
            }
        )

    exact = [row["coin"] for row in rows if row["proof_class"] == "exact_config_smoke"]
    inferred = [row["coin"] for row in rows if row["proof_class"] == "inferred_config_smoke"]
    return {
        "generated_at": utc_now_iso(),
        "config_path": str(CONFIG_PATH),
        "runner_path": str(SCRIPTS / "multi_coin_isolated_runner.py"),
        "leadership_read": [
            "This manifest gives the room a canonical way to smoke the approved sleeve book through the isolated runner override path.",
            "Exact-config smokes should be treated as stronger evidence than inferred-config smokes, because some sleeve rows still rely on family-level reconstruction rather than one saved param artifact.",
            "That means TRU, NOM, SUP, and BAL can be smoked honestly now against the sleeve book, while RAVE, A8, and CFG remain useful but slightly weaker config-evidence cases.",
        ],
        "summary": {
            "rows": len(rows),
            "exact_config_smokes": exact,
            "inferred_config_smokes": inferred,
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Sleeve Smoke Manifest",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Coin | Board Strategy | Runner Strategy | Proof Class | Smoke Command |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            f"| {row['coin']} | {row['board_strategy']} | {row['runner_strategy']} | {row['proof_class']} | `{row['smoke_command']}` |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
