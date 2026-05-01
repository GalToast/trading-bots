#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import adaptive_lattice_shadow_runner as runner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a generic regime-switch shadow plan from an existing lane baseline."
    )
    parser.add_argument("--lane-name", required=True, help="Baseline lane name from runner registry")
    parser.add_argument("--symbol", required=True, help="Target symbol for regime-aware selection")
    parser.add_argument("--registry-path", default=str(runner.DEFAULT_REGISTRY_PATH))
    parser.add_argument("--shape-library-path", default=str(runner.DEFAULT_SHAPE_LIBRARY_PATH))
    parser.add_argument("--regime-path", default=str(runner.DEFAULT_REGIME_PATH))
    parser.add_argument("--runtime-audit-path", default="")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbol = str(args.symbol)
    runtime_audit_path = Path(args.runtime_audit_path) if str(args.runtime_audit_path).strip() else None
    plan = runner.build_plan(
        lane_name=str(args.lane_name),
        symbol=symbol,
        registry_path=Path(args.registry_path),
        shape_library_path=Path(args.shape_library_path),
        regime_path=Path(args.regime_path),
        runtime_audit_path=runtime_audit_path,
    )
    default_json, default_md = runner.default_plan_output_paths(symbol)
    output_json = Path(args.output_json) if str(args.output_json).strip() else default_json
    output_md = Path(args.output_md) if str(args.output_md).strip() else default_md
    output_json.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    runner.write_markdown(plan, output_md)
    print(f"Wrote {output_json}")
    print(f"Wrote {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
