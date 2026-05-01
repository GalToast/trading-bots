"""
Regime-Gated GBPUSD M15 Sweep
Complements @health-check's tuning analysis by testing ADX/ATR regime filtering.
Tests: skip bars when trending (ADX > threshold), only trade ranging bars.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "GBPUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
BARS = 1000  # ~10 trading days

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_bars():
    mt5.initialize()
    bars = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, BARS)
    mt5.shutdown()
    return bars

def compute_atr(bars, period=14, end_idx=None):
    if end_idx is None:
        end_idx = len(bars)
    if end_idx < period + 1:
        return None
    trs = []
    for j in range(max(1, end_idx - period), end_idx):
        tr = max(
            bars[j]['high'] - bars[j]['low'],
            abs(bars[j]['high'] - bars[j-1]['close']),
            abs(bars[j]['low'] - bars[j-1]['close'])
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None

def compute_adx(bars, period=14, end_idx=None):
    """Simplified ADX approximation using ATR ratio as trend strength proxy."""
    if end_idx is None or end_idx < period * 2:
        return 0
    # Use ATR change as trend proxy: if ATR is rising, trend is strong
    atr_now = compute_atr(bars, period, end_idx)
    atr_prev = compute_atr(bars, period, end_idx - period)
    if atr_now and atr_prev and atr_prev > 0:
        ratio = atr_now / atr_prev
        # A rough ADX proxy: ratio > 1.5 means strong trend
        return min(50, max(0, (ratio - 0.5) * 30))
    return 25  # neutral

def simulate_regime_gated(bars, step_buy, step_sell, alpha, max_open,
                          adx_threshold=None, adx_skip_trending=True):
    """Simulate lattice with optional regime gating."""
    if bars is None or len(bars) < 200:
        return {'error': 'insufficient data'}

    anchor = bars[100]['close']  # Skip first 100 bars for ATR warmup
    next_buy = anchor - step_buy
    next_sell = anchor + step_sell
    buy_positions = []
    sell_positions = []
    closes = []
    resets = 0
    total_pnl = 0.0
    skipped_bars = 0
    traded_bars = 0

    for i in range(101, len(bars)):
        bar = bars[i]
        high = bar['high']
        low = bar['low']
        close = bar['close']

        # Regime gate: skip if trending (ADX above threshold)
        if adx_threshold is not None and i >= 114:
            adx = compute_adx(bars, 14, i)
            if adx_skip_trending and adx > adx_threshold:
                skipped_bars += 1
                continue

        traded_bars += 1

        # Check for fills
        if high >= next_sell and len(sell_positions) < max_open:
            sell_positions.append({'entry': next_sell, 'idx': i})
            next_sell += step_sell

        if low <= next_buy and len(buy_positions) < max_open:
            buy_positions.append({'entry': next_buy, 'idx': i})
            next_buy -= step_buy

        # Check for close (alpha-based: close profitable positions)
        if buy_positions or sell_positions:
            buy_to_close = []
            sell_to_close = []

            for pos in buy_positions:
                if close > pos['entry']:  # profitable
                    buy_to_close.append(pos)

            for pos in sell_positions:
                if close < pos['entry']:  # profitable
                    sell_to_close.append(pos)

            if buy_to_close or sell_to_close:
                # Close alpha fraction of profitable positions
                all_profitable = buy_to_close + sell_to_close
                n_to_close = max(1, int(len(all_profitable) * alpha))

                for pos in all_profitable[:n_to_close]:
                    if pos in buy_to_close:
                        pnl = (close - pos['entry']) * 100000 * 0.01  # pip value
                    else:
                        pnl = (pos['entry'] - close) * 100000 * 0.01
                    closes.append({'pnl': pnl, 'idx': i, 'dir': 'BUY' if pos in buy_to_close else 'SELL'})
                    total_pnl += pnl
                    if pos in buy_positions:
                        buy_positions.remove(pos)
                    if pos in sell_positions:
                        sell_positions.remove(pos)

        # Anchor reset: if price moves too far from anchor
        if abs(close - anchor) > step_buy * 10:
            resets += 1
            anchor = close
            next_buy = anchor - step_buy
            next_sell = anchor + step_sell

    n_closes = len(closes)
    avg_pnl = total_pnl / n_closes if n_closes > 0 else 0

    return {
        'net_pnl': round(total_pnl, 2),
        'closes': n_closes,
        'avg_pnl_per_close': round(avg_pnl, 4),
        'resets': resets,
        'skipped_bars': skipped_bars,
        'traded_bars': traded_bars,
        'skip_rate': round(skipped_bars / (skipped_bars + traded_bars) * 100, 1) if (skipped_bars + traded_bars) > 0 else 0,
        'final_open': len(buy_positions) + len(sell_positions),
    }

def main():
    print("=== REGIME-GATED GBPUSD M15 SWEEP ===")
    print(f"Loading {BARS} bars of {SYMBOL} M15...")
    bars = load_bars()
    if bars is None:
        print("Failed to load bars")
        return

    print(f"Loaded {len(bars)} bars, from {datetime.fromtimestamp(bars[0]['time'])} to {datetime.fromtimestamp(bars[-1]['time'])}")

    # Base config from Hungry Hippo
    base_step_buy = 0.00027
    base_step_sell = 0.00013
    base_alpha = 0.5
    base_max_open = 12

    # Test matrix: ADX thresholds
    adx_thresholds = [None, 20, 25, 30, 35]
    results = []

    for adx_thresh in adx_thresholds:
        label = "no_gate" if adx_thresh is None else f"adx>{adx_thresh}"
        result = simulate_regime_gated(bars, base_step_buy, base_step_sell,
                                       base_alpha, base_max_open,
                                       adx_threshold=adx_thresh)
        result['config'] = f"step_buy={base_step_buy}, step_sell={base_step_sell}, alpha={base_alpha}"
        result['regime_gate'] = label
        results.append(result)
        print(f"  {label}: net=${result['net_pnl']}, closes={result['closes']}, "
              f"$/c={result['avg_pnl_per_close']}, resets={result['resets']}, "
              f"skip={result['skip_rate']}%")

    # Find best
    best = max(results, key=lambda x: x['net_pnl'])
    print(f"\nBest config: {best['regime_gate']}")
    print(f"  Net PnL: ${best['net_pnl']}")
    print(f"  Closes: {best['closes']}")
    print(f"  $/close: ${best['avg_pnl_per_close']}")
    print(f"  Skipped bars: {best['skip_rate']}%")

    # Save results
    output = {
        'generated_at': utc_now_iso(),
        'symbol': SYMBOL,
        'timeframe': 'M15',
        'bars': len(bars),
        'base_config': {
            'step_buy': base_step_buy,
            'step_sell': base_step_sell,
            'alpha': base_alpha,
            'max_open': base_max_open,
        },
        'results': results,
        'best': best,
    }

    out_path = ROOT / 'reports' / 'hungry_hippo_regime_gate_sweep.json'
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to {out_path}")

if __name__ == '__main__':
    main()
