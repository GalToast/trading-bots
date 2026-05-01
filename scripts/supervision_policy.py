#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_VERSION = "2026-04-12-finality-1"

BASE_POLICY: dict[str, Any] = {
    "restart_storm_window_seconds": 1800,
    "restart_storm_max_restarts": 4,
    "quarantine_seconds": 1800,
    "incident_cluster_gap_seconds": 600,
    "exact_fire_support": "full_trigger_recompute",
    "exact_fire_label": "full trigger recompute",
    "exact_fire_operator_note": "",
}

KIND_OVERRIDES: dict[str, dict[str, Any]] = {
    "live_crypto": {},
    "shadow_crypto": {},
    "live_fx": {},
    "shadow_fx": {},
    "shadow_unified": {},
    "shadow_coinbase_futures": {
        "exact_fire_support": "limited_state_validation",
        "exact_fire_label": "limited state validation",
        "exact_fire_operator_note": "coinbase futures shadows do not expose full trigger recompute in the current monitor",
    },
    "shadow_coinbase_spot": {
        "restart_storm_window_seconds": 2400,
        "restart_storm_max_restarts": 5,
        "quarantine_seconds": 2400,
        "incident_cluster_gap_seconds": 900,
        "exact_fire_support": "limited_state_validation",
        "exact_fire_label": "limited state validation",
        "exact_fire_operator_note": "coinbase spot shadows rely on state/event checks unless explicit trigger fields exist",
    },
}

NAME_PREFIX_OVERRIDES: list[tuple[str, dict[str, Any]]] = [
    (
        "shadow_coinbase_experimental_",
        {
            "restart_storm_window_seconds": 1800,
            "restart_storm_max_restarts": 3,
            "quarantine_seconds": 3600,
            "incident_cluster_gap_seconds": 1200,
            "exact_fire_support": "state_parity_only",
            "exact_fire_label": "state parity only",
            "exact_fire_operator_note": "experimental coinbase shadows are not exact-trigger verified yet",
        },
    ),
]


def lane_policy(kind: str, name: str = "") -> dict[str, Any]:
    lane_kind = str(kind or "").strip().lower()
    lane_name = str(name or "").strip()
    policy = deepcopy(BASE_POLICY)
    policy.update(deepcopy(KIND_OVERRIDES.get(lane_kind, {})))
    for prefix, overrides in NAME_PREFIX_OVERRIDES:
        if lane_name.startswith(prefix):
            policy.update(deepcopy(overrides))
    policy["policy_version"] = POLICY_VERSION
    policy["kind"] = lane_kind
    policy["name"] = lane_name
    return policy


def exact_fire_policy(kind: str, name: str = "") -> dict[str, Any]:
    policy = lane_policy(kind, name)
    return {
        "support": str(policy["exact_fire_support"]),
        "label": str(policy["exact_fire_label"]),
        "operator_note": str(policy["exact_fire_operator_note"]),
        "policy_version": str(policy["policy_version"]),
    }


def restart_policy(kind: str, name: str = "") -> dict[str, Any]:
    policy = lane_policy(kind, name)
    return {
        "window_seconds": int(policy["restart_storm_window_seconds"]),
        "max_restarts": int(policy["restart_storm_max_restarts"]),
        "quarantine_seconds": int(policy["quarantine_seconds"]),
        "policy_version": str(policy["policy_version"]),
    }


def incident_cluster_gap_seconds(kind: str, name: str = "") -> int:
    return int(lane_policy(kind, name)["incident_cluster_gap_seconds"])


def policy_snapshot() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, int, int]] = set()
    for lane_kind, overrides in KIND_OVERRIDES.items():
        policy = lane_policy(lane_kind, "")
        row = {
            "scope": lane_kind,
            "restart_storm_window_seconds": int(policy["restart_storm_window_seconds"]),
            "restart_storm_max_restarts": int(policy["restart_storm_max_restarts"]),
            "quarantine_seconds": int(policy["quarantine_seconds"]),
            "incident_cluster_gap_seconds": int(policy["incident_cluster_gap_seconds"]),
            "exact_fire_support": str(policy["exact_fire_support"]),
            "exact_fire_label": str(policy["exact_fire_label"]),
            "exact_fire_operator_note": str(policy["exact_fire_operator_note"]),
        }
        key = (
            row["scope"],
            row["exact_fire_support"],
            row["exact_fire_label"],
            row["restart_storm_window_seconds"],
            row["restart_storm_max_restarts"],
            row["quarantine_seconds"],
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    for prefix, _ in NAME_PREFIX_OVERRIDES:
        policy = lane_policy("shadow_coinbase_spot", prefix)
        rows.append(
            {
                "scope": prefix + "*",
                "restart_storm_window_seconds": int(policy["restart_storm_window_seconds"]),
                "restart_storm_max_restarts": int(policy["restart_storm_max_restarts"]),
                "quarantine_seconds": int(policy["quarantine_seconds"]),
                "incident_cluster_gap_seconds": int(policy["incident_cluster_gap_seconds"]),
                "exact_fire_support": str(policy["exact_fire_support"]),
                "exact_fire_label": str(policy["exact_fire_label"]),
                "exact_fire_operator_note": str(policy["exact_fire_operator_note"]),
            }
        )
    return rows
