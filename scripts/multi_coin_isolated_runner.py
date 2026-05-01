#!/usr/bin/env python3
"""
Multi-Coin Isolated Bankroll Runner — One process, N independent sub-ledgers.

Each coin gets a FIXED bankroll and trades independently. No coin can drain another's capital.
Solves the shared pool degradation problem (0.38% retention → 100% retention).

Usage:
    python scripts/multi_coin_isolated_runner.py --total-cash 48
    python scripts/multi_coin_isolated_runner.py --total-cash 100 --coins RAVE-USD NOM-USD
    python scripts/multi_coin_isolated_runner.py --config-path reports/coinbase_isolated_runner_sleeve_book_config.json --total-cash 48
"""
import json
import os
import sys
import io

# Force UTF-8 output on Windows consoles that default to cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from regime_score import RollingRegimeScore

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_isolated_events.jsonl"
HEARTBEAT_PATH = ROOT / "reports" / "multi_coin_isolated_heartbeat.json"  # Touched every cycle for watchdog detection

# OPTIMAL configs from optimal_portfolio_optimizer.py
# Maximizes total PnL: $234/mo at $48, $4,383/mo at $900
# fibonacci_breakout: NOM, GHST, SUP (3 coins, $2,630/mo at $100)
# supertrend: RAVE, TRU, BAL, IOTX (4 coins, $1,585/mo at $100)
# momentum: IOTX, CFG (2 coins, $168/mo at $100)
DEFAULT_COIN_CONFIGS = [
    {"coin": "NOM-USD",   "strategy": "fibonacci",  "fib_lookback": 20, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    # GHST: reduced fib_lookback (20→10) because Coinbase returns only ~11 five-min
    # candles for GHST-USD in a 120-min window. Original needed 25 candles min,
    # now needs only 15. With ~11 backfill + ~1/cycle, fires after ~4 cycles.
    {"coin": "GHST-USD",  "strategy": "fibonacci",  "fib_lookback": 10, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 96},
    {"coin": "SUP-USD",   "strategy": "fibonacci",  "fib_lookback": 20, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    {"coin": "RAVE-USD",  "strategy": "supertrend", "supertrend_atr_period": 10, "supertrend_atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.05, "max_hold": 48},
    {"coin": "TRU-USD",   "strategy": "supertrend", "supertrend_atr_period": 10, "supertrend_atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    {"coin": "BAL-USD",   "strategy": "supertrend", "supertrend_atr_period": 10, "supertrend_atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.05, "max_hold": 96},
    {"coin": "IOTX-USD",  "strategy": "supertrend", "supertrend_atr_period": 10, "supertrend_atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    {"coin": "IOTX-USD",  "strategy": "momentum",   "lookback": 10, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 48},
    {"coin": "CFG-USD",   "strategy": "momentum",   "lookback": 15, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 48},
]
COIN_CONFIGS = [dict(cfg) for cfg in DEFAULT_COIN_CONFIGS]

SESSION_DEAD_HOURS = {0, 6, 12, 19}
SESSION_BYPASS = False  # Set to True to disable session gate (for proof runs)

# Per-coin session hour whitelists — from session_hour_consolidation.py analysis.
# Each coin gets only its top-6 profitable hours. Overrides SESSION_DEAD_HOURS
# when a coin has an entry in this dict. Set USE_PER_COIN_HOURS=False to revert.
#
# REGIME SHIFT NOTE (2026-04-13): Fresh 30d verification shows 7/9 coins now
# produce MORE PnL with all active hours vs gated. Only A8/CFG (momentum) still
# benefit from top-6 gating. Net: all-hours $69.39 vs gated $62.43 (+$6.96/mo).
# Gate is ENABLED for specific coins showing high session sensitivity (NOM, A8, CFG).
USE_PER_COIN_HOURS = True
PER_COIN_SESSION_HOURS = {
    "NOM-USD":  {5, 8, 9, 13, 17, 21},      # Optimized 2026-04-14: $+164 expectancy
    "A8-USD":   {7, 11, 15, 17, 22, 23},    # already optimal
    "CFG-USD":  {0, 1, 4, 8, 10, 13, 20},   # +hour 0: 63.6% WR, positive
}
FETCH_LOOKBACK_MINUTES = 120
MIN_CASH_PER_POSITION = 2.0  # Lower threshold for smaller bankrolls
DEPLOY_FRACTION = 0.90  # Deploy 90% of coin's bankroll per trade

# BTC regime gate — only enter altcoin longs when BTC is trending up
# Lab test #1 proved BTC leads alts by 1-3 bars. This is our free leading indicator.
BTC_REGIME_GATE_ENABLED = True  # Set False to disable
BTC_REGIME_LOOKBACK = 20  # Number of M5 candles to compute BTC momentum
BTC_REGIME_MIN_MOMENTUM = 0.002  # BTC must be up >= 0.2% over lookback
btc_candle_cache = []  # Populated each cycle with (start, close) tuples


def fetch_and_update_btc_regime(client):
    """Fetch recent BTC-USD M5 candles and update the global cache.
    
    Returns the BTC momentum percentage over the lookback period.
    Positive = uptrend, Negative = downtrend.
    """
    global btc_candle_cache
    try:
        now = int(time.time())
        start = now - (BTC_REGIME_LOOKBACK + 5) * 300  # Extra buffer
        resp = client.market_candles("BTC-USD", start=start, end=now, granularity="FIVE_MINUTE")
        candles = resp.get("candles", [])
        # Parse: [start, low, high, open, close, volume]
        parsed = []
        for c in candles:
            parsed.append({
                "start": int(c[0]),
                "low": float(c[1]),
                "high": float(c[2]),
                "open": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
        # Sort by time, deduplicate by start timestamp
        parsed.sort(key=lambda x: x["start"])
        seen = set()
        unique = []
        for c in parsed:
            if c["start"] not in seen:
                seen.add(c["start"])
                unique.append(c)
        btc_candle_cache = unique[-BTC_REGIME_LOOKBACK:]
        
        if len(btc_candle_cache) >= 3:
            first_close = btc_candle_cache[0]["close"]
            last_close = btc_candle_cache[-1]["close"]
            momentum = (last_close - first_close) / first_close
            return momentum
        return None
    except Exception as e:
        print(f"  [WARN] BTC regime fetch failed: {e}", flush=True)
        return None


def btc_regime_allows_entry():
    """Check if BTC momentum gate allows new altcoin entries.
    
    Returns True if:
    - Gate is disabled (BTC_REGIME_GATE_ENABLED = False), OR
    - BTC momentum is positive (>= min threshold), OR
    - BTC data is unavailable (fail-open to avoid missing trades)
    """
    if not BTC_REGIME_GATE_ENABLED:
        return True
    
    if len(btc_candle_cache) < 3:
        # Not enough data yet — fail open for first few cycles
        return True
    
    momentum = None
    try:
        first_close = btc_candle_cache[0]["close"]
        last_close = btc_candle_cache[-1]["close"]
        momentum = (last_close - first_close) / first_close
    except (KeyError, IndexError, ZeroDivisionError):
        return True  # Fail open on data errors
    
    if momentum is None:
        return True
    
    allowed = momentum >= BTC_REGIME_MIN_MOMENTUM
    return allowed


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def recover_state_from_events(event_path: Path, coin_names: list[str]) -> dict:
    """
    Reconstruct close counts, win/loss records, and cumulative realized PnL
    from the append-only event log. This makes the runner self-healing after
    restarts that lose state file history.

    Returns: {coin: {"closes": N, "wins": N, "losses": N, "realized_pnl": float}}
    """
    if not event_path.exists():
        return {}

    recovered = {}
    for coin in coin_names:
        recovered[coin] = {"closes": 0, "wins": 0, "losses": 0, "realized_pnl": 0.0}

    try:
        with event_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue

                if evt.get("action") != "close":
                    continue

                coin = evt.get("coin", "")
                if coin not in recovered:
                    continue

                net = evt.get("net", 0.0)
                recovered[coin]["closes"] += 1
                recovered[coin]["realized_pnl"] += net
                if net >= 0:
                    recovered[coin]["wins"] += 1
                else:
                    recovered[coin]["losses"] += 1
    except Exception as e:
        print(f"  [WARN] Event log recovery failed: {e}", flush=True)

    # Only return entries that have actual data
    return {k: v for k, v in recovered.items() if v["closes"] > 0}


def load_runner_configs(config_path: str | None) -> list[dict]:
    if not config_path:
        return [dict(cfg) for cfg in DEFAULT_COIN_CONFIGS]

    path = Path(config_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = raw.get("configs") or raw.get("rows") or []
    else:
        raise ValueError(f"Unsupported config payload in {path}")

    configs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        coin = str(row.get("coin") or "").strip()
        strategy = str(row.get("strategy") or "").strip()
        if not coin or not strategy:
            continue
        configs.append(dict(row))

    if not configs:
        raise ValueError(f"No usable config rows found in {path}")
    return configs


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


class CoinLedger:
    """Independent sub-ledger for one coin. Has its own cash, positions, PnL."""
    def __init__(self, cfg, starting_cash):
        self.coin = cfg["coin"]
        self.strategy = cfg["strategy"]
        self.lookback = cfg.get("lookback", 20)
        self.range_lookback = cfg.get("range_lookback", cfg.get("lookback", 20))
        self.reg_period = cfg.get("reg_period", 20)
        self.supertrend_atr_period = cfg.get("supertrend_atr_period", 10)
        self.supertrend_atr_mult = cfg.get("supertrend_atr_mult", 3.0)
        self.fib_lookback = cfg.get("fib_lookback", 20)
        self.fib_level = cfg.get("fib_level", 0.618)  # 0.618 golden ratio
        self.tp_pct = cfg.get("tp_pct", 0.10)
        self.sl_pct = cfg.get("sl_pct", 0.03)
        self.max_hold = cfg.get("max_hold", 48)
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.history = []
        self.candle_history = []
        self.last_candle_time = 0
        self.position = None
        self.last_signal_price = 0.0  # Prevent re-entry on same signal after crash/restart
        self.last_signal_time = 0     # Absolute time-based deduplication guard
        self.signals = 0
        self.closes = 0
        self.wins = 0
        self.losses = 0

    def _supertrend_signal(self):
        """Supertrend indicator: trend-following using ATR bands."""
        atr_period = self.supertrend_atr_period
        atr_mult = self.supertrend_atr_mult
        if len(self.candle_history) < atr_period + 5:
            return False, None

        # Calculate ATR
        trs = []
        for i in range(1, len(self.candle_history)):
            c = self.candle_history[i]
            cp = self.candle_history[i-1]
            tr = max(
                float(c["high"]) - float(c["low"]),
                abs(float(c["high"]) - float(cp["close"])),
                abs(float(c["low"]) - float(cp["close"]))
            )
            trs.append(tr)

        if len(trs) < atr_period:
            return False, None

        atr = sum(trs[-atr_period:]) / atr_period

        # Supertrend bands
        hl2 = (float(self.candle_history[-1]["high"]) + float(self.candle_history[-1]["low"])) / 2
        upper = hl2 + atr_mult * atr
        lower = hl2 - atr_mult * atr

        # Buy signal: price above lower band (uptrend)
        current = float(self.candle_history[-1]["close"])
        is_uptrend = current > lower

        # REGIME FILTER: 200 EMA trend confirmation (hyperdrive improvement)
        # Only take longs in confirmed uptrends — avoids counter-trend chopping
        if is_uptrend and len(self.candle_history) >= 200:
            closes_200 = [float(c["close"]) for c in self.candle_history[-200:]]
            ema_200 = sum(closes_200) / 200  # Simple approximation — good enough for filter
            if current < ema_200:
                # Price is below 200 EMA — skip signal even if supertrend says uptrend
                return False, lower

        return is_uptrend, lower

    def _fibonacci_breakout_signal(self):
        """Fibonacci breakout: buy when price breaks above Fib retracement level.

        Hyperdrive improvements:
        1. Volume confirmation — breakout must have above-average volume
        2. Minimum breakout threshold — price must exceed fib level by min %
        3. Momentum confirmation — previous 3 candles must show upward pressure
        """
        if len(self.candle_history) < self.fib_lookback + 5:
            return False

        # Find recent high and low
        recent = self.candle_history[-self.fib_lookback:]
        highs = [float(c["high"]) for c in recent]
        lows = [float(c["low"]) for c in recent]
        period_high = max(highs)
        period_low = min(lows)

        # Fibonacci retracement level
        fib_level = getattr(self, 'fib_level', 0.618)  # 0.618 golden ratio, with fallback
        fib_price = period_high - (period_high - period_low) * fib_level

        # Current price and breakout strength
        current = float(self.candle_history[-1]["close"])
        breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0

        # IMPROVEMENT 1: Minimum breakout threshold (2% above fib level)
        # Prevents marginal breakouts that whipsaw — @qwen-2 identified 78% fee drag
        min_breakout_pct = 0.02
        if breakout_pct < min_breakout_pct:
            return False

        # IMPROVEMENT 2: Volume confirmation — current candle volume > 20-period avg
        # Filters low-conviction breakouts that lack participation
        if len(self.candle_history) >= 20:
            volumes = [float(c.get("volume", 0)) for c in self.candle_history[-20:]]
            avg_volume = sum(volumes) / len(volumes) if volumes else 0
            current_volume = float(self.candle_history[-1].get("volume", 0))
            if avg_volume > 0 and current_volume < avg_volume * 0.8:
                # Volume is below 80% of average — low conviction breakout
                return False

        # IMPROVEMENT 3: Momentum confirmation — last 3 candles show upward pressure
        # At least 2 of last 3 candles must be green (close > open)
        if len(self.candle_history) >= 3:
            recent_candles = self.candle_history[-3:]
            green_count = sum(1 for c in recent_candles if float(c["close"]) > float(c["open"]))
            if green_count < 2:
                # Not enough upward momentum — breakout lacks conviction
                return False

        return True

    def _theil_sen_signal(self):
        """Theil-Sen estimator: mean reversion signal."""
        if len(self.history) < self.reg_period + 5:
            return False
        recent = self.history[-self.reg_period:]
        n = len(recent)
        x = list(range(n))
        y = recent
        slopes = []
        for i in range(0, n - 1, 2):
            if x[i + 1] - x[i] != 0:
                slopes.append((y[i + 1] - y[i]) / (x[i + 1] - x[i]))
        if not slopes:
            return False
        med_slope = sorted(slopes)[len(slopes) // 2]
        med_y = sorted(y)[len(y) // 2]
        med_x = sorted(x)[len(x) // 2]
        intercept = med_y - med_slope * med_x
        predicted = med_slope * n + intercept
        actual = y[-1]
        deviation = (predicted - actual) / actual
        return deviation < -0.02  # Mean reversion buy signal

    def process_candles(self, candles, *, backfill=False):
        events = []
        fee_rate = get_fee_rate(self.total_volume)

        for candle in candles:
            ts = int(candle["start"])
            close = float(candle["close"])
            high = float(candle["high"])
            low = float(candle["low"])
            open_price = float(candle["open"])

            # Skip invalid candles
            if open_price <= 0 or close <= 0 or high <= 0 or low <= 0:
                continue

            self.history.append(close)
            self.candle_history.append(candle)
            if len(self.history) > 500:
                self.history = self.history[-500:]
                self.candle_history = self.candle_history[-500:]
            self.last_candle_time = ts

            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

            # Session gate: per-coin hour whitelists if enabled, else legacy dead hours
            if SESSION_BYPASS:
                session_open = True
            elif USE_PER_COIN_HOURS and self.coin in PER_COIN_SESSION_HOURS:
                session_open = hour in PER_COIN_SESSION_HOURS[self.coin]
            else:
                session_open = hour not in SESSION_DEAD_HOURS

            # EXIT
            if self.position:
                self.position["hold"] += 1
                exit_price = None
                exit_reason = None

                if high >= self.position["tp"]:
                    exit_price = self.position["tp"]
                    exit_reason = "tp"
                elif self.sl_pct > 0 and low <= self.position["sl"]:
                    exit_price = self.position["sl"]
                    exit_reason = "stop"
                elif self.position["hold"] >= self.max_hold:
                    exit_price = close
                    exit_reason = "timeout"

                if exit_price is not None:
                    units = self.position["units"]
                    gross = (exit_price - self.position["ep"]) * units
                    entry_fee = self.position["entry_fee"]
                    exit_fee = exit_price * units * fee_rate
                    net = gross - entry_fee - exit_fee

                    self.cash += self.position["q"] + net
                    self.closes += 1
                    if net > 0:
                        self.wins += 1
                    else:
                        self.losses += 1
                    self.total_volume += self.position["q"] + (exit_price * units)
                    self.total_fees += entry_fee + exit_fee

                    event = {
                        "ts_utc": utc_now_iso(),
                        "coin": self.coin,
                        "action": "close",
                        "exit_price": round(exit_price, 12),
                        "entry_price": round(self.position["ep"], 12),
                        "net": round(net, 4),
                        "reason": exit_reason,
                        "hold_bars": self.position["hold"],
                        "fees": round(entry_fee + exit_fee, 4),
                        "strategy": self.strategy,
                    }
                    events.append(event)
                    self.position = None

            # ENTRY (skip during backfill AND during session dead hours)
            if not backfill and self.position is None and self.cash >= MIN_CASH_PER_POSITION and session_open:
                signal_fired = False
                # BTC regime gate — skip entry if BTC is dumping (we're long-only)
                if not btc_regime_allows_entry():
                    pass  # BTC regime blocks entry — skip silently
                elif self.strategy == "momentum":
                    if len(self.candle_history) > self.lookback + 1:
                        recent_high = max(float(c["high"]) for c in self.candle_history[-(self.lookback+1):-1])
                        # Deduplication guard: only fire if price exceeds last signal level
                        if high > recent_high and high > self.last_signal_price:
                            signal_fired = True
                elif self.strategy == "range_breakout":
                    if len(self.candle_history) > self.range_lookback + 1:
                        recent_high = max(float(c["high"]) for c in self.candle_history[-(self.range_lookback+1):-1])
                        if high > recent_high and high > self.last_signal_price:
                            signal_fired = True
                elif self.strategy == "theil_sen":
                    if self._theil_sen_signal() and high > self.last_signal_price:
                        signal_fired = True
                elif self.strategy == "supertrend":
                    is_uptrend, _ = self._supertrend_signal()
                    if is_uptrend and high > self.last_signal_price:
                        signal_fired = True
                elif self.strategy == "fibonacci":
                    if self._fibonacci_breakout_signal() and high > self.last_signal_price:
                        signal_fired = True
                elif self.strategy == "rsi_mr":
                    if len(self.history) > 4:
                        rsi_val = compute_rsi(self.history[:-1], 3)
                        if rsi_val <= 30 and high > self.last_signal_price:
                            signal_fired = True

                if signal_fired:
                    # Absolute time-based deduplication: never fire more than once on the same timestamp
                    if ts <= getattr(self, 'last_signal_time', 0):
                        signal_fired = False
                    else:
                        self.last_signal_time = ts

                if signal_fired:
                    self.signals += 1
                    self.last_signal_price = max(self.last_signal_price, high)  # Record signal level BEFORE any state save to prevent re-entry
                    deploy = self.cash * DEPLOY_FRACTION
                    entry_price = open_price

                    # Guard against zero
                    if entry_price <= 0:
                        continue

                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / entry_price
                    tp = entry_price * (1 + self.tp_pct)
                    sl = entry_price * (1 - self.sl_pct) if self.sl_pct > 0 else 0

                    self.cash -= deploy
                    self.position = {
                        "ep": entry_price,
                        "q": deploy,
                        "units": units,
                        "tp": tp,
                        "sl": sl,
                        "hold": 0,
                        "entry_fee": entry_fee,
                        "max_hold": self.max_hold,
                    }

                    event = {
                        "ts_utc": utc_now_iso(),
                        "coin": self.coin,
                        "strategy": self.strategy,
                        "action": "open",
                        "entry_price": round(entry_price, 12),
                        "tp": round(tp, 12),
                        "sl": round(sl, 12),
                        "deploy": round(deploy, 4),
                        "entry_bar_start": ts,
                    }
                    events.append(event)

        return events

    def snapshot(self):
        wr = self.wins / max(1, self.closes) * 100
        # FIXED: equity = realized cash only, NOT including deployed position value.
        # The deployed capital (position["q"]) is subtracted from cash on entry,
        # so counting it again as "equity" double-counts money that's still at risk.
        equity = self.cash
        pnl = equity - self.starting_cash
        snap = {
            "coin": self.coin,
            "strategy": self.strategy,
            "starting_cash": round(self.starting_cash, 4),
            "cash": round(self.cash, 4),
            "equity": round(equity, 4),
            "pnl": round(pnl, 4),
            "return_pct": round(pnl / self.starting_cash * 100, 2),
            "signals": self.signals,
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(wr, 1),
            "total_volume": round(self.total_volume, 4),
            "total_fees": round(self.total_fees, 4),
            "position": "active" if self.position else "flat",
            "position_entry": round(self.position["ep"], 12) if self.position else None,
            "position_hold": self.position["hold"] if self.position else None,
            "position_tp": round(self.position["tp"], 12) if self.position else None,
            "position_sl": round(self.position["sl"], 12) if self.position else None,
            "position_units": self.position["units"] if self.position else None,
            "position_deploy": round(self.position["q"], 4) if self.position else None,
            "position_entry_fee": round(self.position["entry_fee"], 6) if self.position else None,
            "position_max_hold": self.position["max_hold"] if self.position else None,
            "last_candle_time": self.last_candle_time,
            "history_len": len(self.history),
            "last_signal_price": round(self.last_signal_price, 12) if self.last_signal_price else 0.0,
            "last_signal_time": self.last_signal_time,
        }
        return snap


def load_state(state_path):
    if not state_path.exists():
        return None
    try:
        with open(state_path) as f:
            return json.load(f)
    except Exception:
        return None


def _touch_heartbeat_maybe(heartbeat_path, cycle, total_equity):
    """Write a lightweight heartbeat file every cycle. Never raises."""
    try:
        hb = {
            "updated_at": utc_now_iso(),
            "cycle": cycle,
            "total_equity": round(total_equity, 4),
            "pid": os.getpid(),
        }
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_path.write_text(json.dumps(hb), encoding="utf-8")
    except Exception as e:
        print(f"  [WARN] heartbeat write failed: {e}", flush=True)


def _save_state_maybe(state_path, cycle, ledgers, total_starting_cash):
    """Best-effort state save for shutdown/error paths. Never raises."""
    try:
        total_equity = sum(
            l.cash + (l.position["q"] if l.position else 0) for l in ledgers.values()
        )
        total_pnl = total_equity - total_starting_cash
        return_pct = total_pnl / total_starting_cash * 100 if total_starting_cash else 0
        state = {
            "updated_at": utc_now_iso(),
            "cycle": cycle,
            "total_starting_cash": total_starting_cash,
            "total_equity": round(total_equity, 4),
            "total_pnl": round(total_pnl, 4),
            "return_pct": round(return_pct, 2),
            "ledgers": {coin: ledger.snapshot() for coin, ledger in ledgers.items()},
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(state_path)
    except Exception as e:
        print(f"  [WARN] _save_state_maybe failed: {e}", flush=True)


def enforce_singleton():
    """Kill any existing duplicate Kelly runner processes."""
    import os
    import psutil
    current_pid = os.getpid()
    script_name = "multi_coin_isolated_runner.py"
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == current_pid:
                continue
            cmdline = proc.info.get('cmdline') or []
            if cmdline and any(script_name in arg for arg in cmdline) and "python" in (proc.info.get('name') or "").lower():
                print(f"  [KILL] Terminating duplicate Kelly runner process (PID {proc.info['pid']})", flush=True)
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

def main():
    enforce_singleton()
    import argparse
    parser = argparse.ArgumentParser(description="Isolated bankroll runner with per-coin strategies")
    parser.add_argument("--total-cash", type=float, default=48.0, help="Total bankroll across all coins")
    parser.add_argument("--coins", nargs="+", default=None, help="Subset of coins to run")
    parser.add_argument("--config-path", type=str, default=None, help="Optional JSON config file overriding the built-in coin configs")
    parser.add_argument("--state-path", type=str, default=None, help="Custom state file path")
    parser.add_argument("--event-path", type=str, default=None, help="Custom events file path")
    parser.add_argument("--heartbeat-path", type=str, default=None, help="Custom heartbeat file path")
    parser.add_argument("--dry-run", action="store_true", help="Backfill only, no live entries")
    parser.add_argument("--max-cycles", type=int, default=0, help="Stop after N cycles (0 = unlimited)")
    parser.add_argument("--no-session-gate", action="store_true", help="Disable session dead hour filter (for proof runs)")
    parser.add_argument("--no-btc-regime-gate", action="store_true", help="Disable BTC momentum regime gate (for proof runs / microcap strategies)")
    args = parser.parse_args()

    # Set session bypass for proof runs
    global SESSION_BYPASS
    SESSION_BYPASS = args.no_session_gate

    # Set BTC regime gate bypass for microcap strategies
    global BTC_REGIME_GATE_ENABLED
    if args.no_btc_regime_gate:
        BTC_REGIME_GATE_ENABLED = False
        print("  ⚡ BTC regime gate DISABLED — microcap strategies can fire regardless of BTC momentum", flush=True)

    # Override paths if specified
    state_path = Path(args.state_path) if args.state_path else STATE_PATH
    event_path = Path(args.event_path) if args.event_path else EVENT_PATH
    heartbeat_path = Path(args.heartbeat_path) if args.heartbeat_path else HEARTBEAT_PATH

    configs = load_runner_configs(args.config_path)
    if args.coins:
        configs = [c for c in configs if c["coin"] in args.coins]
    if not configs:
        raise SystemExit("No runner configs selected after applying --coins/--config-path filters.")

    n = len(configs)
    per_coin_cash = args.total_cash / n

    client = CoinbaseAdvancedClient()

    # ORPHANED POSITION CHECK — verify exchange matches local state
    if not args.dry_run:
        print("\nChecking for orphaned positions on exchange...", flush=True)
        try:
            open_orders = client.list_orders(order_status="OPEN", limit=100)
            open_order_list = open_orders.get("orders", []) if isinstance(open_orders, dict) else []
            exchange_coins = set()
            for order in open_order_list:
                pid = order.get("product_id", "")
                # Normalize to runner format (e.g., "RAVE-USD")
                if "-" not in pid and len(pid) > 6:
                    # Could be "RAVEUSD" format — skip for now
                    continue
                exchange_coins.add(pid.upper())

            local_coins = set(cfg["coin"].upper() for cfg in configs)
            orphaned = exchange_coins - local_coins
            if orphaned:
                print(f"  [WARN] ORPHANED POSITIONS on exchange: {orphaned}", flush=True)
                print(f"  These coins have open orders but are NOT in this runner's config!", flush=True)
                print(f"  Consider closing them manually before starting.", flush=True)
            else:
                print(f"  [OK] No orphaned positions ({len(open_order_list)} open orders, all match config)", flush=True)
        except Exception as e:
            print(f"  [WARN] Could not check orphaned positions: {e}", flush=True)
            print(f"  Proceeding anyway — verify manually if concerned.", flush=True)

    # Try to load previous state
    prev_state = load_state(state_path)

    # Initialize ledgers
    ledgers = {}
    regime_trackers = {}  # coin -> RollingRegimeScore (shadow signal)
    for cfg in configs:
        starting = per_coin_cash
        # Recover from previous state if available
        if prev_state and "ledgers" in prev_state:
            prev = prev_state["ledgers"].get(cfg["coin"])
            if prev:
                prev_cash = prev.get("cash", per_coin_cash)
                prev_starting = prev.get("starting_cash", per_coin_cash)
                # Only recover cash if the per-coin allocation is similar (within 2x)
                # This prevents loading stale state from different bankroll configs
                if 0.5 * per_coin_cash <= prev_starting <= 2.0 * per_coin_cash:
                    starting = prev_cash
                else:
                    # Different allocation - reset cash but keep position if active
                    print(f"  {cfg['coin']}: resetting cash (prev ${prev_starting:.2f} vs current ${per_coin_cash:.2f})", flush=True)
        ledger = CoinLedger(cfg, starting)

        # Restore position and history from previous state
        if prev_state and "ledgers" in prev_state:
            prev = prev_state["ledgers"].get(cfg["coin"])
            if prev and prev.get("position") == "active" and prev.get("position_entry") is not None:
                ep = prev["position_entry"]
                if ep > 0:
                    ledger.position = {
                        "ep": ep,
                        "q": prev.get("position_deploy", 0),
                        "units": prev.get("position_units", 0),
                        "tp": prev.get("position_tp", 0),
                        "sl": prev.get("position_sl", 0),
                        "hold": prev.get("position_hold", 0),
                        "entry_fee": prev.get("position_entry_fee", 0),
                        "max_hold": prev.get("position_max_hold", ledger.max_hold),
                    }
                    # Restore cumulative stats
                    ledger.signals = prev.get("signals", 0)
                    ledger.closes = prev.get("closes", 0)
                    ledger.wins = prev.get("wins", 0)
                    ledger.losses = prev.get("losses", 0)
                    ledger.total_volume = prev.get("total_volume", 0)
                    ledger.total_fees = prev.get("total_fees", 0)
                    # Restore last_candle_time so backfill doesn't re-process old candles
                    ledger.last_candle_time = prev.get("last_candle_time", 0)
                    # Restore signal deduplication state to prevent re-entry on same signal after crash
                    ledger.last_signal_price = prev.get("last_signal_price", 0.0)
                    ledger.last_signal_time = prev.get("last_signal_time", 0)
                    print(f"  RECOVERED {cfg['coin']}: entry=${ep:.6f}, hold={ledger.position['hold']} bars, last_ts={ledger.last_candle_time}", flush=True)
        ledgers[cfg["coin"]] = ledger
        regime_trackers[cfg["coin"]] = RollingRegimeScore(window=20)

    cycle = prev_state.get("cycle", 0) if prev_state else 0
    # Reset cycle counter when max-cycles is specified (bounded proof run)
    if args.max_cycles > 0:
        cycle = 0
    total_starting_cash = args.total_cash

    # RECOVER STATE FROM EVENT LOG (self-healing after restarts)
    # The event log is append-only and survives restarts even when state files reset.
    # Merge recovered close counts, wins/losses, and realized PnL into ledgers.
    coin_names = [cfg["coin"] for cfg in configs]
    recovered = recover_state_from_events(event_path, coin_names)
    total_recovered_pnl = 0.0
    for coin, stats in recovered.items():
        if coin in ledgers:
            ledger = ledgers[coin]
            # Only apply if the event log has MORE closes than the state file
            # (handles the case where state was reset but events were preserved)
            if stats["closes"] > ledger.closes:
                ledger.closes = stats["closes"]
                ledger.wins = stats["wins"]
                ledger.losses = stats["losses"]
                ledger.realized_pnl = stats["realized_pnl"]
                total_recovered_pnl += stats["realized_pnl"]
                print(f"  📜 RECOVERED from event log: {coin}: {stats['closes']} closes, "
                      f"{stats['wins']}W/{stats['losses']}L, PnL=${stats['realized_pnl']:+.4f}", flush=True)

    # Adjust starting cash to include recovered PnL so equity displays correctly
    if total_recovered_pnl != 0:
        total_starting_cash += total_recovered_pnl
        print(f"  📜 Adjusted starting cash: ${args.total_cash:.2f} → ${total_starting_cash:.2f} (includes ${total_recovered_pnl:+.4f} recovered PnL)", flush=True)

    # Backfill
    now = int(time.time())
    start = now - FETCH_LOOKBACK_MINUTES * 60
    print(f"=" * 70, flush=True)
    mode = "DRY RUN (backfill only)" if args.dry_run else "LIVE"
    print(f"  ISOLATED BANKROLL RUNNER — {mode}", flush=True)
    print(f"  Coins: {', '.join(c['coin'] for c in configs)}", flush=True)
    print(f"  Total cash: ${args.total_cash:.2f} -> ${per_coin_cash:.2f}/coin ({n} coins)", flush=True)
    if args.config_path:
        print(f"  Config path: {args.config_path}", flush=True)
    if args.max_cycles > 0:
        print(f"  Max cycles: {args.max_cycles}", flush=True)
    print(f"=" * 70, flush=True)

    print(f"\nBackfilling {FETCH_LOOKBACK_MINUTES}min of history...", flush=True)
    for cfg in configs:
        coin = cfg["coin"]
        ledger = ledgers[coin]
        try:
            candles = fetch_candles(client, coin, start, now)
            if candles:
                # Skip candles already seen by recovered positions
                if ledger.last_candle_time > 0:
                    candles = [c for c in candles if int(c["start"]) > ledger.last_candle_time]
                if candles:
                    events = ledger.process_candles(candles, backfill=True)
                    print(f"  {coin}: {len(candles)} new candles, {len(events)} exits", flush=True)
                else:
                    print(f"  {coin}: no new candles (fully recovered)", flush=True)
            else:
                print(f"  {coin}: NO CANDLES", flush=True)
        except Exception as e:
            print(f"  {coin}: BACKFILL ERROR — {e}", flush=True)

    # Log start
    append_jsonl(event_path, {
        "ts_utc": utc_now_iso(),
        "action": "runner_start_isolated",
        "total_cash": args.total_cash,
        "per_coin_cash": per_coin_cash,
        "coins": [c["coin"] for c in configs],
        "mode": "dry_run" if args.dry_run else "live",
    })

    if args.dry_run:
        print("\nDRY RUN complete. No live entries.", flush=True)
        total_equity = sum(l.cash for l in ledgers.values())
        return 0

    print(f"\nLIVE STARTED: ${args.total_cash:.2f} total, ${per_coin_cash:.2f}/coin", flush=True)

    # Live loop
    try:
        while True:
            cycle += 1

            # Max cycles check
            if args.max_cycles > 0 and cycle > args.max_cycles:
                print(f"\nMax cycles ({args.max_cycles}) reached. Stopping.", flush=True)
                break
            try:
                now = int(time.time())
                all_events = []

                for cfg in configs:
                    coin = cfg["coin"]
                    ledger = ledgers[coin]
                    start_fetch = ledger.last_candle_time or (now - 600)

                    try:
                        candles = fetch_candles(client, coin, start_fetch, now)
                        new_candles = [c for c in candles if int(c["start"]) > ledger.last_candle_time]

                        if new_candles:
                            events = ledger.process_candles(new_candles)
                            all_events.extend(events)
                    except Exception as e:
                        print(f"  ERR {coin}: {e}", flush=True)

                # Regime score shadow signal — computes oscillation vs trend for each coin
                regime_scores = {}
                for coin in ledgers:
                    ledger = ledgers[coin]
                    tracker = regime_trackers.get(coin)
                    if tracker and ledger.candle_history:
                        r = tracker.update(ledger.candle_history[-40:])  # last 40 candles
                        regime_scores[coin] = r

                # Print regime scores (shadow — doesn't gate entries yet)
                if regime_scores:
                    regime_str = " | ".join(
                        f"{c}: {r['score']:+.2f}({r['regime'][:3]})"
                        for c, r in sorted(regime_scores.items())
                    )
                    print(f"  REGIME: {regime_str}", flush=True)

                # BTC regime check — fetch BTC candles once per cycle
                if not args.dry_run and BTC_REGIME_GATE_ENABLED:
                    btc_momentum = fetch_and_update_btc_regime(client)

                # Log events
                has_open = False
                for evt in all_events:
                    append_jsonl(event_path, evt)
                    if evt.get("action") == "open":
                        has_open = True
                    pnl_class = "+" if evt.get("net", 0) >= 0 else ""
                    print(f"  EVT: {evt['coin']} {evt['action']} {evt.get('entry_price', evt.get('exit_price', '?'))} net={pnl_class}${evt.get('net', 0):.2f}", flush=True)

                # CRITICAL: Save state immediately after position opens to prevent loss on crash
                if has_open:
                    try:
                        total_equity_now = sum(l.cash for l in ledgers.values())
                        total_pnl_now = total_equity_now - total_starting_cash
                        return_pct_now = total_pnl_now / total_starting_cash * 100 if total_starting_cash else 0
                        emergency_state = {
                            "updated_at": utc_now_iso(),
                            "cycle": cycle,
                            "total_starting_cash": total_starting_cash,
                            "per_coin_cash": per_coin_cash,
                            "total_equity": round(total_equity_now, 4),
                            "total_pnl": round(total_pnl_now, 4),
                            "return_pct": round(return_pct_now, 2),
                            "ledgers": {
                                coin: ledger.snapshot() for coin, ledger in ledgers.items()
                            },
                        }
                        state_path.parent.mkdir(parents=True, exist_ok=True)
                        tmp_path = state_path.with_suffix(".tmp")
                        tmp_path.write_text(json.dumps(emergency_state, indent=2, sort_keys=True), encoding="utf-8")
                        tmp_path.replace(state_path)
                        print(f"  💾 EMERGENCY STATE SAVE after open events (cycle {cycle})", flush=True)
                    except Exception as e:
                        print(f"  [WARN] Emergency state save failed: {e}", flush=True)

                # CRITICAL: Periodic state save EVERY cycle to prevent position loss on crash
                # This ensures active positions are always persisted even if no new open occurs this cycle
                try:
                    total_equity_now = sum(l.cash for l in ledgers.values())
                    total_pnl_now = total_equity_now - total_starting_cash
                    return_pct_now = total_pnl_now / total_starting_cash * 100 if total_starting_cash else 0
                    periodic_state = {
                        "updated_at": utc_now_iso(),
                        "cycle": cycle,
                        "total_starting_cash": total_starting_cash,
                        "per_coin_cash": per_coin_cash,
                        "total_equity": round(total_equity_now, 4),
                        "total_pnl": round(total_pnl_now, 4),
                        "return_pct": round(return_pct_now, 2),
                        "ledgers": {
                            coin: ledger.snapshot() for coin, ledger in ledgers.items()
                        },
                    }
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path = state_path.with_suffix(".tmp")
                    tmp_path.write_text(json.dumps(periodic_state, indent=2, sort_keys=True), encoding="utf-8")
                    tmp_path.replace(state_path)
                except Exception as e:
                    print(f"  [WARN] Periodic state save failed: {e}", flush=True)

                # Calculate total equity
                total_equity = sum(l.cash for l in ledgers.values())
                total_pnl = total_equity - total_starting_cash
                return_pct = total_pnl / total_starting_cash * 100

                # Save state — MUST NOT fail silently
                state = {
                    "updated_at": utc_now_iso(),
                    "cycle": cycle,
                    "total_starting_cash": total_starting_cash,
                    "per_coin_cash": per_coin_cash,
                    "total_equity": round(total_equity, 4),
                    "total_pnl": round(total_pnl, 4),
                    "return_pct": round(return_pct, 2),
                    "ledgers": {
                        coin: ledger.snapshot() for coin, ledger in ledgers.items()
                    },
                }
                try:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    # Write to temp file first, then rename — atomic on most filesystems
                    tmp_path = state_path.with_suffix(".tmp")
                    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
                    tmp_path.replace(state_path)
                except Exception as e:
                    print(f"  [WARN] STATE WRITE FAILED cycle {cycle}: {e}", flush=True)
                    traceback.print_exc()
                    # Try to write a minimal fallback state
                    try:
                        fallback = {
                            "updated_at": utc_now_iso(),
                            "cycle": cycle,
                            "total_equity": round(total_equity, 4),
                            "warning": "state_write_failed",
                        }
                        state_path.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
                        print(f"  Wrote minimal fallback state for cycle {cycle}", flush=True)
                    except Exception as e2:
                        print(f"  [WARN] FALLBACK STATE ALSO FAILED: {e2}", flush=True)

                # Heartbeat — lightweight file touched every cycle for watchdog detection
                _touch_heartbeat_maybe(heartbeat_path, cycle, total_equity)

                # Watchdog: check if state file is stale (not updated in >5 cycles)
                # This catches silent write failures that previously killed the runner at cycle 20
                try:
                    state_mtime = state_path.stat().st_mtime if state_path.exists() else 0
                    seconds_since_write = time.time() - state_mtime
                    if seconds_since_write > 180:  # 3 minutes = ~6 cycles at 30s each
                        print(f"  🚨 WATCHDOG: State file is {seconds_since_write:.0f}s stale! Last write may have failed.", flush=True)
                        print(f"  WATCHDOG: Attempting emergency state rewrite...", flush=True)
                        _save_state_maybe(state_path, cycle, ledgers, total_starting_cash)
                except Exception as e:
                    print(f"  [WARN] Watchdog check failed: {e}", flush=True)

                # Print heartbeat
                active = sum(1 for l in ledgers.values() if l.position)
                total_signals = sum(l.signals for l in ledgers.values())
                total_closes = sum(l.closes for l in ledgers.values())
                total_wins = sum(l.wins for l in ledgers.values())
                wr = total_wins / max(1, total_closes) * 100

                print(
                    f"HB#{cycle}: equity=${total_equity:.2f} pnl=${total_pnl:+.2f} "
                    f"({return_pct:+.1f}%) | pos={active}/{n} | "
                    f"signals={total_signals} closes={total_closes} wr={wr:.1f}%",
                    flush=True
                )

            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"EXC in cycle {cycle}: {e}", flush=True)
                traceback.print_exc()
                # Save state on error so cycle count is not lost
                _save_state_maybe(state_path, cycle, ledgers, total_starting_cash)

            time.sleep(30)

    except KeyboardInterrupt:
        print(f"\nShutting down after {cycle} cycles...", flush=True)
        total_equity = sum(l.cash for l in ledgers.values())
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "runner_stop",
            "total_equity": round(total_equity, 4),
            "cycle": cycle,
        })
        # Save state file on clean shutdown so cycle count persists
        _save_state_maybe(state_path, cycle, ledgers, total_starting_cash)
        print("State saved. Done.", flush=True)


if __name__ == "__main__":
    main()
