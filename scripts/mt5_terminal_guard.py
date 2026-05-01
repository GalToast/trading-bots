#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def _load_repo_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def _env_text(name: str) -> str:
    _load_repo_env()
    return str(os.environ.get(name, "") or "").strip()


def _parse_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.rstrip("\\/").replace("/", "\\").lower()


def _terminal_path_matches(expected_path: Any, actual_path: Any) -> bool:
    expected_norm = _normalize_path(expected_path)
    actual_norm = _normalize_path(actual_path)
    if not expected_norm or not actual_norm:
        return False
    if expected_norm == actual_norm:
        return True
    if expected_norm.endswith("\\terminal64.exe") and expected_norm[: -len("\\terminal64.exe")] == actual_norm:
        return True
    return False


def expected_contract() -> dict[str, Any]:
    terminal_path = _env_text("MT5_TERMINAL_PATH") or _env_text("MT5_EXPECTED_TERMINAL_PATH")
    return {
        "expected_login": _parse_int(_env_text("MT5_LOGIN")),
        "expected_server": _env_text("MT5_SERVER"),
        "expected_terminal_path": terminal_path,
        "terminal_path_configured": bool(terminal_path),
        "binding_mode": "path_pinned" if terminal_path else "account_only",
    }


def _initialize_kwargs(contract: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if str(contract.get("expected_terminal_path") or "").strip():
        kwargs["path"] = str(contract["expected_terminal_path"])
    if int(contract.get("expected_login") or 0) > 0:
        kwargs["login"] = int(contract["expected_login"])
    password = _env_text("MT5_PASSWORD")
    if password:
        kwargs["password"] = password
    server = str(contract.get("expected_server") or "").strip()
    if server:
        kwargs["server"] = server
    return kwargs


def connected_identity(mt5_module: Any = mt5) -> dict[str, Any]:
    account = mt5_module.account_info()
    terminal = mt5_module.terminal_info()
    return {
        "connected": True,
        "login": _parse_int(getattr(account, "login", 0) if account else 0),
        "server": str(getattr(account, "server", "") if account else ""),
        "trade_allowed": bool(getattr(terminal, "trade_allowed", False) if terminal else False),
        "terminal_connected": bool(getattr(terminal, "connected", False) if terminal else False),
        "terminal_path": str(getattr(terminal, "path", "") if terminal else ""),
        "last_error": mt5_module.last_error(),
    }


def identity_mismatches(
    contract: dict[str, Any],
    identity: dict[str, Any],
    *,
    require_trade_allowed: bool,
) -> list[str]:
    mismatches: list[str] = []
    expected_login = int(contract.get("expected_login") or 0)
    if expected_login > 0 and int(identity.get("login") or 0) != expected_login:
        mismatches.append("login_mismatch")
    expected_server = str(contract.get("expected_server") or "").strip().lower()
    current_server = str(identity.get("server") or "").strip().lower()
    if expected_server and current_server != expected_server:
        mismatches.append("server_mismatch")
    expected_path = str(contract.get("expected_terminal_path") or "").strip()
    if expected_path and not _terminal_path_matches(expected_path, identity.get("terminal_path")):
        mismatches.append("terminal_path_mismatch")
    if require_trade_allowed and not bool(identity.get("trade_allowed")):
        mismatches.append("trade_disabled")
    return mismatches


def initialize_mt5(
    *,
    mt5_module: Any = mt5,
    require_trade_allowed: bool = False,
    shutdown_on_failure: bool = True,
) -> tuple[bool, dict[str, Any]]:
    contract = expected_contract()
    kwargs = _initialize_kwargs(contract)
    connected = bool(mt5_module.initialize(**kwargs)) if kwargs else bool(mt5_module.initialize())
    payload: dict[str, Any] = {
        "contract": contract,
        "initialize_kwargs": {
            key: value
            for key, value in kwargs.items()
            if key != "password"
        },
    }
    if not connected:
        payload.update(
            {
                "connected": False,
                "identity_ok": False,
                "reason": "initialize_failed",
                "identity_mismatches": ["initialize_failed"],
                "last_error": mt5_module.last_error(),
            }
        )
        return False, payload

    identity = connected_identity(mt5_module=mt5_module)
    payload.update(identity)
    mismatches = identity_mismatches(contract, identity, require_trade_allowed=require_trade_allowed)
    payload["identity_mismatches"] = mismatches
    payload["identity_ok"] = not mismatches
    payload["reason"] = "ok" if not mismatches else "identity_mismatch"
    if mismatches and shutdown_on_failure:
        try:
            mt5_module.shutdown()
        except Exception:
            pass
    return not mismatches, payload


def failure_summary(payload: dict[str, Any]) -> str:
    reason = str(payload.get("reason") or "unknown")
    mismatches = list(payload.get("identity_mismatches") or [])
    joined = ",".join(mismatches) if mismatches else reason
    login = int(payload.get("login") or 0)
    server = str(payload.get("server") or "") or "-"
    terminal_path = str(payload.get("terminal_path") or "") or "-"
    return (
        f"MT5 connection guard failed: {joined} "
        f"(login={login or '-'} server={server} terminal={terminal_path})"
    )
