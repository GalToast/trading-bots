"""Run a timed benchmark for one trading bot and record broker equity drift."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oanda_config import get_oanda_config


def read_local_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _sanitize_profile(profile: str | None) -> str | None:
    if not profile:
        return None
    return re.sub(r"[^A-Za-z0-9_]", "_", profile.strip())


def _resolve_credential(base_name: str, env_values: dict[str, str], profile: str | None) -> str | None:
    if not profile:
        direct = os.getenv(base_name) or env_values.get(base_name)
        return direct

    normalized = _sanitize_profile(profile)
    if not normalized:
        direct = os.getenv(base_name) or env_values.get(base_name)
        return direct

    aliases = []
    for candidate in (
        profile,
        normalized,
        normalized.upper(),
        normalized.lower(),
        normalized.strip("_"),
        normalized.strip("_").upper(),
        normalized.strip("_").lower(),
    ):
        if candidate and candidate not in aliases:
            aliases.append(candidate)

    for alias in aliases:
        env_name = f"{base_name}_{alias}"
        direct_alias = os.getenv(env_name) or env_values.get(env_name)
        if direct_alias:
            return direct_alias

    direct = os.getenv(base_name) or env_values.get(base_name)
    return direct


def alpaca_config(profile: str | None = None) -> tuple[str, str, str]:
    env_values = read_local_env()
    effective_profile = profile or os.getenv("ALPACA_PROFILE")
    api_key = _resolve_credential("ALPACA_API_KEY", env_values, effective_profile)
    secret_key = _resolve_credential("ALPACA_SECRET_KEY", env_values, effective_profile)
    if not api_key or not secret_key:
        raise RuntimeError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY")

    base_url = os.getenv("ALPACA_BASE_URL") or env_values.get("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets"
    return api_key, secret_key, base_url


def alpaca_equity(profile: str | None = None) -> float:
    api_key, secret_key, base_url = alpaca_config(profile)
    response = requests.get(
        f"{base_url}/v2/account",
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        },
        timeout=15,
    )
    response.raise_for_status()
    return float(response.json()["equity"])


def flatten_alpaca_positions(profile: str | None = None) -> None:
    api_key, secret_key, base_url = alpaca_config(profile)
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    response = requests.get(f"{base_url}/v2/positions", headers=headers, timeout=15)
    if response.status_code != 200:
        return

    positions = response.json()
    if not isinstance(positions, list):
        return

    for position in positions:
        symbol = position.get("symbol")
        if not symbol:
            continue
        requests.delete(f"{base_url}/v2/positions/{symbol}", headers=headers, timeout=15)


def oanda_nav() -> float:
    cfg = get_oanda_config()
    response = requests.get(
        f"{cfg['api_base_v3']}/accounts/{cfg['account_id']}/summary",
        headers={
            "Authorization": f"Bearer {cfg['api_token']}",
            "Content-Type": cfg["content_type"],
        },
        timeout=15,
    )
    response.raise_for_status()
    account = response.json()["account"]
    return float(account.get("NAV", account.get("balance", 0)))


def broker_equity(broker: str, profile: str | None = None) -> float:
    if broker == "alpaca":
        return alpaca_equity(profile)
    if broker == "oanda":
        return oanda_nav()
    raise ValueError(f"Unsupported broker: {broker}")


def terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def build_equity_timeline(
    started_at: datetime,
    start_equity: float,
    samples: list[dict[str, float | str]],
    finished_at: datetime,
    end_equity: float,
) -> list[dict[str, float | str]]:
    timeline: list[dict[str, float | str]] = [
        {
            "timestamp": started_at.isoformat(),
            "equity": start_equity,
            "source": "start",
        }
    ]
    for sample in samples:
        timeline.append(
            {
                "timestamp": sample["timestamp"],
                "equity": sample["equity"],
                "source": "poll",
            }
        )
    timeline.append(
        {
            "timestamp": finished_at.isoformat(),
            "equity": end_equity,
            "source": "end",
        }
    )
    return timeline


def compute_equity_stats(timeline: list[dict[str, float | str]]) -> dict[str, float | str]:
    first_point = timeline[0]
    peak_equity = float(first_point["equity"])
    trough_equity = peak_equity
    peak_at = str(first_point["timestamp"])
    trough_at = peak_at
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    max_drawdown_peak_at = peak_at
    max_drawdown_trough_at = peak_at

    for point in timeline:
        equity = float(point["equity"])
        timestamp = str(point["timestamp"])
        if equity > peak_equity:
            peak_equity = equity
            peak_at = timestamp
        if equity < trough_equity:
            trough_equity = equity
            trough_at = timestamp

        drawdown = peak_equity - equity
        drawdown_pct = (drawdown / peak_equity * 100.0) if peak_equity else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_drawdown_pct = drawdown_pct
            max_drawdown_peak_at = peak_at
            max_drawdown_trough_at = timestamp

    return {
        "peak_equity": peak_equity,
        "peak_at": peak_at,
        "trough_equity": trough_equity,
        "trough_at": trough_at,
        "peak_multiple": (peak_equity / float(first_point["equity"])) if float(first_point["equity"]) else 0.0,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_peak_at": max_drawdown_peak_at,
        "max_drawdown_trough_at": max_drawdown_trough_at,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", choices=["alpaca", "oanda"], required=True)
    parser.add_argument("--bot", required=True, help="Absolute or relative path to bot file")
    parser.add_argument("--alpaca-profile", default=None, help="Optional profile suffix for ALPACA_API_KEY/SECRET")
    parser.add_argument("--duration", type=int, default=300, help="Benchmark duration in seconds")
    parser.add_argument("--poll", type=int, default=30, help="Equity snapshot cadence in seconds")
    parser.add_argument(
        "--out-dir",
        default="reports/bot-benchmarks",
        help="Directory for JSON benchmark results",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run ID suffix for output filenames.",
    )
    args = parser.parse_args()

    bot_path = Path(args.bot).resolve()
    if not bot_path.exists():
        raise FileNotFoundError(bot_path)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().astimezone()
    alpaca_profile = args.alpaca_profile or os.getenv("ALPACA_PROFILE")
    start_equity = broker_equity(args.broker, alpaca_profile)

    run_id = args.run_id or f"{started_at.strftime('%Y%m%d-%H%M%S-%f')}"
    log_path = out_dir / f"{bot_path.stem}-{run_id}.log"
    bot_exit_code_during_benchmark: int | None = None
    bot_exit_at_during_benchmark: str | None = None
    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(bot_path)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

        samples: list[dict[str, float | str]] = []
        deadline = time.time() + args.duration
        try:
            while time.time() < deadline:
                time.sleep(min(args.poll, max(1, int(deadline - time.time()))))
                equity = broker_equity(args.broker, alpaca_profile)
                samples.append(
                    {
                        "timestamp": datetime.now().astimezone().isoformat(),
                        "equity": equity,
                    }
                )
                exit_code = proc.poll()
                if exit_code is not None:
                    bot_exit_code_during_benchmark = exit_code
                    bot_exit_at_during_benchmark = datetime.now().astimezone().isoformat()
                    break
        finally:
            terminate_process(proc)
            if args.broker == "alpaca":
                flatten_alpaca_positions(alpaca_profile)
                time.sleep(1)

    end_equity = broker_equity(args.broker, alpaca_profile)
    finished_at = datetime.now().astimezone()
    equity_timeline = build_equity_timeline(started_at, start_equity, samples, finished_at, end_equity)
    equity_stats = compute_equity_stats(equity_timeline)

    result = {
        "broker": args.broker,
        "alpaca_profile": alpaca_profile,
        "bot": str(bot_path),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "start_equity": start_equity,
        "end_equity": end_equity,
        "peak_equity": equity_stats["peak_equity"],
        "peak_at": equity_stats["peak_at"],
        "trough_equity": equity_stats["trough_equity"],
        "trough_at": equity_stats["trough_at"],
        "peak_multiple": equity_stats["peak_multiple"],
        "max_drawdown": equity_stats["max_drawdown"],
        "max_drawdown_pct": equity_stats["max_drawdown_pct"],
        "max_drawdown_peak_at": equity_stats["max_drawdown_peak_at"],
        "max_drawdown_trough_at": equity_stats["max_drawdown_trough_at"],
        "pnl": end_equity - start_equity,
        "return_pct": ((end_equity - start_equity) / start_equity * 100) if start_equity else 0.0,
        "bot_exit_code_during_benchmark": bot_exit_code_during_benchmark,
        "bot_exit_at_during_benchmark": bot_exit_at_during_benchmark,
        "bot_exited_during_benchmark": bot_exit_code_during_benchmark is not None,
        "samples": samples,
        "equity_timeline": equity_timeline,
        "log_path": str(log_path),
    }

    result_path = out_dir / f"{bot_path.stem}-{run_id}.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
