"""Spark-2 Alpaca mean-reversion snapback bot.

Theme: overextension mean-reversion. The bot looks for a pair stretched
below its rolling z-score band and enters only when a micro-rebound appears.
"""

import statistics
import time
from datetime import datetime

import requests
from alpaca_config import get_alpaca_config

ALPACA = get_alpaca_config()
BASE_URL = ALPACA["base_url"]
DATA_URL = ALPACA["data_url"]

HEADERS = {
    "APCA-API-KEY-ID": ALPACA["api_key"],
    "APCA-API-SECRET-KEY": ALPACA["secret_key"],
}

# Keep momentum fast enough for rapid compounding, but avoid overtrading.
PAIRS = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD", "LTC/USD", "BCH/USD", "DOGE/USD", "UNI/USD"]

LOOKBACK_WINDOW = 20
MAX_BARS = 80

OVEREXTENSION_Z = -0.75
REBOUND_MIN = -0.05  # allow immediate knife-catch if z-score is stretched enough
TP_PCT = 0.0014
SL_PCT = 0.0035
TRAIL_TRIGGER_PCT = 0.0007
TRAIL_DROP_PCT = 0.0005
MAX_HOLD_CYCLES = 2

BASE_POSITION_PCT = 0.90
MAX_POSITION_PCT = 0.99
MIN_POSITION_PCT = 0.55

TARGET_MULTIPLIER = 10.0
CYCLE_INTERVAL = 4
DUST_QTY = 1e-6
ENTRY_COOLDOWN_SECONDS = 45

position_state = {}
entry_cooldowns = {}
consecutive_wins = 0
consecutive_losses = 0
cycle_count = 0


def get_account_info():
    response = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=12)
    if response.status_code != 200:
        print(f"[ACCOUNT] {response.status_code}: {response.text[:140]}")
        return None
    return response.json()


def get_open_positions():
    response = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=12)
    if response.status_code != 200:
        return []
    positions = response.json()
    if not isinstance(positions, list):
        return []
    cleaned = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        qty_raw = position.get("qty")
        symbol = position.get("symbol")
        try:
            qty = abs(float(qty_raw or 0))
        except (TypeError, ValueError):
            qty = 0.0
        if not symbol or qty <= DUST_QTY:
            continue
        cleaned.append(position)
    return cleaned


def has_pending_orders():
    response = requests.get(f"{BASE_URL}/v2/orders", headers=HEADERS, params={"status": "open"}, timeout=12)
    if response.status_code != 200:
        return False
    orders = response.json()
    return isinstance(orders, list) and any(isinstance(order, dict) for order in orders)


def get_latest_price(symbol):
    response = requests.get(
        f"{DATA_URL}/latest/trades",
        headers=HEADERS,
        params={"symbols": symbol},
        timeout=12,
    )
    if response.status_code != 200:
        return None
    data = response.json().get("trades", {})
    return data.get(symbol, {}).get("p")


def get_crypto_bars(symbol):
    response = requests.get(
        f"{DATA_URL}/bars",
        headers=HEADERS,
        params={"symbols": symbol, "timeframe": "1Min", "limit": MAX_BARS},
        timeout=12,
    )
    if response.status_code != 200:
        return []

    bars = response.json().get("bars", {}).get(symbol, [])
    return [float(bar["c"]) for bar in bars if bar.get("c") is not None]


def analyze_pair(symbol, closes):
    if len(closes) < LOOKBACK_WINDOW + 3:
        return None

    window = closes[-LOOKBACK_WINDOW:]
    mean = sum(window) / LOOKBACK_WINDOW
    std = statistics.pstdev(window)
    if std <= 0:
        return None

    latest = closes[-1]
    z = (latest - mean) / std
    snap_back_3m = (closes[-1] - closes[-3]) / closes[-3] * 100
    momentum_1m = (closes[-1] - closes[-2]) / closes[-2] * 100

    return {
        "symbol": symbol,
        "latest": latest,
        "z": z,
        "mean": mean,
        "snap_back_3m": snap_back_3m,
        "momentum_1m": momentum_1m,
    }


def get_signal_for(symbol):
    closes = get_crypto_bars(symbol)
    if not closes:
        return None

    metrics = analyze_pair(symbol, closes)
    if not metrics:
        return None

    # SNAP-BACK ENTRY: extreme downside z-score + immediate rebound.
    if (
        metrics["z"] <= OVEREXTENSION_Z
        and (
            metrics["snap_back_3m"] >= REBOUND_MIN
            or metrics["momentum_1m"] >= -0.05
            or metrics["z"] <= OVEREXTENSION_Z - 0.35
        )
    ):
        metrics["side"] = "buy"
        metrics["strength"] = min(1.0, abs(metrics["z"]) / 3.0)
        return metrics

    return None


def position_notional(equity):
    target_pct = BASE_POSITION_PCT
    if consecutive_wins >= 2:
        target_pct = min(MAX_POSITION_PCT, target_pct + 0.12 * consecutive_wins)
    if consecutive_losses:
        target_pct = max(MIN_POSITION_PCT, target_pct - 0.09 * consecutive_losses)
    return equity * target_pct


def place_order(symbol, qty, side):
    response = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "gtc",
        },
        timeout=12,
    )
    if response.status_code not in (200, 201):
        print(f"[ORDER FAILED] {symbol} {side.upper()} {qty} -> {response.status_code}")
        print(response.text[:140])
        return None
    return response.json()


def close_position(symbol):
    response = requests.delete(f"{BASE_URL}/v2/positions/{symbol}", headers=HEADERS, timeout=12)
    if response.status_code not in (200, 201, 204):
        print(f"[CLOSE FAILED] {symbol}: {response.status_code}")
        return False
    return True


def evaluate_position_exits(account_equity):
    global consecutive_wins, consecutive_losses, cycle_count

    for position in get_open_positions():
        symbol = position.get("symbol")
        qty = float(position.get("qty", 0))
        if qty == 0:
            continue

        state = position_state.setdefault(
            symbol,
            {
                "side": "long" if qty > 0 else "short",
                "entry_cycle": cycle_count,
                "peak": float(position.get("avg_entry_price", 0)),
            },
        )
        side = state["side"]

        latest = get_latest_price(symbol)
        if latest is None:
            continue

        entry = float(position.get("avg_entry_price", latest))
        closes = get_crypto_bars(symbol)
        metrics = analyze_pair(symbol, closes) if closes else None
        z = metrics.get("z") if metrics else None

        if side == "long":
            pnl_pct = (latest - entry) / entry
            if latest > state["peak"]:
                state["peak"] = latest
            exit_reason = None

            if pnl_pct >= TP_PCT:
                exit_reason = f"TP {pnl_pct*100:.2f}%"
            elif pnl_pct <= -SL_PCT:
                exit_reason = f"SL {pnl_pct*100:.2f}%"
            elif pnl_pct >= TRAIL_TRIGGER_PCT and latest < state["peak"] * (1 - TRAIL_DROP_PCT):
                exit_reason = "TRAILING"
            elif cycle_count - state["entry_cycle"] >= MAX_HOLD_CYCLES and z is not None and z > -0.4:
                exit_reason = "TIME/MEAN"

            if exit_reason and close_position(symbol):
                pnl_cash = (latest - entry) * qty
                if pnl_cash >= 0:
                    consecutive_wins += 1
                    consecutive_losses = 0
                else:
                    consecutive_wins = 0
                    consecutive_losses += 1
                entry_cooldowns[symbol] = time.time() + ENTRY_COOLDOWN_SECONDS
                position_state.pop(symbol, None)
                print(
                    f"[EXIT] {symbol} side={side} qty={qty:.6f} {exit_reason} "
                    f"equity=${account_equity:.2f}"
                )
        else:
            # Shorting not enabled in this bot profile.
            close_position(symbol)


def open_best_snapback(order_equity):
    if get_open_positions() or has_pending_orders():
        return

    signals = []
    for pair in PAIRS:
        if time.time() < entry_cooldowns.get(pair.replace("/", ""), 0):
            continue
        signal = get_signal_for(pair)
        if not signal:
            continue
        signals.append(signal)

    if not signals:
        return

    signals.sort(key=lambda item: (abs(item["z"]) + max(0.0, item["momentum_1m"])), reverse=True)
    signal = signals[0]

    notional = position_notional(order_equity)
    price = signal["latest"]
    if price <= 0:
        return

    qty = notional / price
    if qty < 0.0001:
        return

    order = place_order(signal["symbol"], qty, signal["side"])
    if order:
        trade_symbol = signal["symbol"].replace("/", "")
        position_state[signal["symbol"]] = {
            "side": signal["side"],
            "entry_cycle": cycle_count,
            "peak": price,
            "signal_z": signal["z"],
            "signal_strength": signal["strength"],
        }
        entry_cooldowns[trade_symbol] = time.time() + ENTRY_COOLDOWN_SECONDS
        print(
            f"[ENTRY] {signal['side'].upper()} {signal['symbol']} | "
            f"qty={qty:.6f} price=${price:.2f} z={signal['z']:.2f} "
            f"notional=${notional:.2f}"
        )


def run():
    global cycle_count
    account = get_account_info()
    if not account:
        raise RuntimeError("Could not load Alpaca account. Check API keys and connectivity.")

    equity = float(account.get("portfolio_value") or account.get("equity") or 0)
    if equity <= 0:
        raise RuntimeError("Account equity is not available. Aborting.")

    start_equity = equity

    print("=" * 70)
    print("SPARK-2 ALPACA MEAN-REVERSION SNAPBACK BOT")
    print(f"Starting equity: ${start_equity:.2f}")
    print(f"Target: {TARGET_MULTIPLIER:.1f}x")
    print(f"Cycle: {CYCLE_INTERVAL}s | Window: {LOOKBACK_WINDOW}")
    print("=" * 70)

    while True:
        cycle_count += 1
        account = get_account_info()
        if account:
            equity = float(account.get("portfolio_value") or account.get("equity") or 0)
        else:
            print("[WARN] skipping cycle: account unavailable")
            time.sleep(CYCLE_INTERVAL)
            continue

        print("\n" + "=" * 70)
        print(f"CYCLE #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Equity: ${equity:.2f} | Multiplier: {equity / start_equity:.2f}x")

        evaluate_position_exits(equity)

        if not get_open_positions():
            open_best_snapback(equity)

        portfolio = get_open_positions()
        print(f"Open positions: {len(portfolio)} | W/L streak: {consecutive_wins}/{consecutive_losses}")

        if equity >= start_equity * TARGET_MULTIPLIER:
            print("=" * 70)
            print(f"TARGET HIT: ${start_equity:.2f} -> ${equity:.2f}")
            print("=" * 70)
            break

        time.sleep(CYCLE_INTERVAL)


if __name__ == "__main__":
    run()
