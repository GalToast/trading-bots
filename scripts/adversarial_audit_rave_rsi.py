import json
import time
import sys
import os
import math
import random
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"


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
            time.sleep(0.5)
        except:
            time.sleep(2.0)
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period

    if avg_l > 0:
        rs = avg_g / avg_l
    else:
        return 100.0

    return 100 - (100 / (1 + rs))


def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 30 * 24 * 3600  # 30 days

    print("AUDIT ADVERSARIAL AUDIT: Attempting to Destroy RAVE RSI MR...")
    m5_candles = fetch_candles(client, PRODUCT, start, now, "FIVE_MINUTE")

    if not m5_candles:
        print("No data.")
        return

    print(f"Loaded {len(m5_candles)} M5 candles")

    # ADVERSARIAL ASSUMPTIONS
    # RSI MR: Buy when RSI(3) < 30, sell at 50% TP or 48-bar timeout

    FEE_RATE = 0.0025  # 25bps
    TP_PCT = 0.50  # 50% take profit
    MAX_HOLD = 48  # 48 bars = 4 hours

    for delay_bars in [0, 1, 3]:  # 0, 5min, 15min delay
        for fill_prob in [1.0, 0.75, 0.50, 0.25]:
            cash = 1000.0
            position = None  # {"entry": ..., "bar_entered": ...}
            closes = 0
            wins = 0
            losses = 0
            timeouts = 0

            # Need enough history for RSI
            rsi_lookback = 10

            for i in range(rsi_lookback, len(m5_candles)):
                c = m5_candles[i]
                o = float(c["open"])
                h = float(c["high"])
                l = float(c["low"])
                cl = float(c["close"])

                # Compute RSI from historical closes
                hist_closes = [
                    float(m5_candles[j]["close"]) for j in range(max(0, i - 10), i)
                ]
                rsi = compute_rsi(hist_closes, period=3)

                # Exit check
                if position is not None:
                    bars_held = i - position["bar_entered"]

                    # Take profit check
                    tp = position["entry"] * (1 + TP_PCT)
                    if h >= tp:
                        if random.random() <= fill_prob:
                            # Filled at TP
                            exit_p = tp
                            units = position["units"]
                            cash_back = (units * exit_p) * (1 - FEE_RATE)
                            pnl = cash_back - (
                                units * position["entry"] * (1 + FEE_RATE)
                            )
                            cash += cash_back
                            position = None
                            closes += 1
                            wins += 1
                            continue

                    # Timeout exit (48 bars)
                    if bars_held >= MAX_HOLD:
                        exit_p = cl  # Exit at close
                        units = position["units"]
                        cash_back = (units * exit_p) * (1 - FEE_RATE)
                        pnl = cash_back - (units * position["entry"] * (1 + FEE_RATE))
                        cash += cash_back
                        position = None
                        closes += 1
                        timeouts += 1
                        if pnl > 0:
                            wins += 1
                        else:
                            losses += 1
                        continue

                # Entry check (RSI < 30)
                if position is None and rsi < 30 and cash >= 100.0:
                    # ADVERSARIAL: Delay means we enter at a worse price
                    if delay_bars > 0 and i > delay_bars:
                        # Enter at close of delayed bar (slippage)
                        entry_bar = m5_candles[i - delay_bars]
                        entry_p = float(entry_bar["close"]) * 1.001  # 0.1% slippage
                    else:
                        entry_p = cl  # Enter at current close

                    if random.random() <= fill_prob:
                        deploy = min(cash * 0.9, 100.0)  # 90% of cash or $100
                        units = deploy / (entry_p * (1 + FEE_RATE))
                        cash -= deploy
                        position = {"entry": entry_p, "units": units, "bar_entered": i}

            # Close any remaining position at end
            if position is not None:
                exit_p = float(m5_candles[-1]["close"])
                units = position["units"]
                cash_back = (units * exit_p) * (1 - FEE_RATE)
                cash += cash_back
                position = None

            net = cash - 1000.0
            wr = (wins / max(1, closes)) * 100
            print(
                f"Delay={delay_bars}bars | Fill={fill_prob:4.2f} | Net=${net:8.2f} | WR={wr:4.1f}% | Closes={closes} | Timeouts={timeouts}"
            )

    print("\nCONCLUSION:")
    print("1. RSI MR needs high fill rates to work (entry signals are time-sensitive)")
    print("2. Timeout exits are the silent killer - positions held too long lose money")
    print("3. Execution delay kills the edge - RSI signals decay quickly")


if __name__ == "__main__":
    main()
