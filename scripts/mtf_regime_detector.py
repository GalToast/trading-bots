"""
Multi-Timeframe Regime Detector — ALL Timeframes
Uses MN, W1, D1, H4, H1, M15 to detect trend direction, strength, AND position within higher-timeframe ranges.
Critical insight: when price is at weekly/monthly extremes, reversals are more likely.
The lattice should tighten steps and flip asymmetry when approaching HTF extremes.
"""
import MetaTrader5 as mt5
import json
import os
from datetime import datetime, timezone

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema_val = sum(values[:period]) / period
    for v in values[period:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val

def adx(bars, period=14):
    if len(bars) < period + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(bars)):
        high, low = bars[i]['high'], bars[i]['low']
        prev_close = bars[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        up_move = high - bars[i-1]['high']
        down_move = bars[i-1]['low'] - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    if len(trs) < period:
        return None
    avg_tr = sum(trs[-period:]) / period
    avg_plus = sum(plus_dm[-period:]) / period
    avg_minus = sum(minus_dm[-period:]) / period
    if avg_tr == 0:
        return 0
    plus_di = (avg_plus / avg_tr) * 100
    minus_di = (avg_minus / avg_tr) * 100
    if plus_di + minus_di == 0:
        return 0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    return dx

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def detect_regime(symbol):
    """Multi-timeframe regime detection across ALL timeframes."""
    mt5.initialize()
    
    tfs = {
        'MN': mt5.TIMEFRAME_MN1,
        'W1': mt5.TIMEFRAME_W1,
        'D1': mt5.TIMEFRAME_D1,
        'H4': mt5.TIMEFRAME_H4,
        'H1': mt5.TIMEFRAME_H1,
        'M15': mt5.TIMEFRAME_M15,
        'M5': mt5.TIMEFRAME_M5,
        'M1': mt5.TIMEFRAME_M1,
    }
    
    result = {'symbol': symbol, 'detected_at': utc_now_iso(), 'timeframes': {}}
    
    for tf_name, tf_val in tfs.items():
        bars = mt5.copy_rates_from_pos(symbol, tf_val, 0, 100)
        if bars is None or len(bars) < 20:
            result['timeframes'][tf_name] = {'error': 'insufficient data'}
            continue
        
        closes = [b['close'] for b in bars]
        current_price = closes[-1]
        
        # RSI
        tf_rsi = rsi(closes)
        
        # EMA
        ema_20 = ema(closes, 20)
        ema_50 = ema(closes, 50)
        
        # ADX
        tf_adx = adx(bars)
        
        # Position within recent range (where are we in the last 20 bars?)
        recent_high = max(b['high'] for b in bars[-20:])
        recent_low = min(b['low'] for b in bars[-20:])
        range_span = recent_high - recent_low
        position_in_range = (current_price - recent_low) / range_span if range_span > 0 else 0.5
        
        # ATR
        atrs = []
        for i in range(1, len(bars)):
            tr = max(bars[i]['high'] - bars[i]['low'],
                     abs(bars[i]['high'] - bars[i-1]['close']),
                     abs(bars[i]['low'] - bars[i-1]['close']))
            atrs.append(tr)
        atr = sum(atrs[-14:]) / 14 if len(atrs) >= 14 else 0
        
        # Trend direction
        if ema_20 and ema_50:
            if ema_20 > ema_50:
                trend = 'UP'
            elif ema_20 < ema_50:
                trend = 'DOWN'
            else:
                trend = 'FLAT'
        else:
            trend = 'FLAT'
        
        result['timeframes'][tf_name] = {
            'trend': trend,
            'rsi': round(tf_rsi, 1) if tf_rsi else None,
            'adx': round(tf_adx, 1) if tf_adx else None,
            'ema_20': round(ema_20, 5) if ema_20 else None,
            'ema_50': round(ema_50, 5) if ema_50 else None,
            'atr': round(atr, 5) if atr else None,
            'price': round(current_price, 5),
            'position_in_range': round(position_in_range, 3),
            'recent_high': round(recent_high, 5),
            'recent_low': round(recent_low, 5),
        }
    
    # === MTF CONSENSUS ===
    tfs_data = result['timeframes']
    
    # Count trends across ALL 8 timeframes
    up_count = sum(1 for tf in ['MN','W1','D1','H4','H1','M15','M5','M1'] if tfs_data.get(tf, {}).get('trend') == 'UP')
    down_count = sum(1 for tf in ['MN','W1','D1','H4','H1','M15','M5','M1'] if tfs_data.get(tf, {}).get('trend') == 'DOWN')
    flat_count = 8 - up_count - down_count

    # RSI extremes on higher timeframes
    htf_rsis = [tfs_data[tf].get('rsi') for tf in ['MN','W1','D1','H4'] if tfs_data[tf].get('rsi') is not None]
    at_extreme_high = any(r > 70 for r in htf_rsis)
    at_extreme_low = any(r < 30 for r in htf_rsis)

    # Position at HTF range extremes
    htf_positions = [tfs_data[tf].get('position_in_range', 0.5) for tf in ['MN','W1','D1','H4'] if 'position_in_range' in tfs_data.get(tf, {})]
    at_range_top = any(p > 0.85 for p in htf_positions) if htf_positions else False
    at_range_bottom = any(p < 0.15 for p in htf_positions) if htf_positions else False

    # MTF regime determination (8 TFs)
    if up_count >= 6:
        mtf_regime = 'STRONG_UPTREND'
    elif up_count >= 5:
        mtf_regime = 'UPTREND'
    elif down_count >= 6:
        mtf_regime = 'STRONG_DOWNTREND'
    elif down_count >= 5:
        mtf_regime = 'DOWNTREND'
    elif up_count == down_count:
        mtf_regime = 'RANGING'
    else:
        mtf_regime = 'TRANSITION'
    
    # CRITICAL: At HTF extremes, flip to reversal anticipation
    if at_extreme_high and at_range_top and up_count >= 3:
        mtf_regime = 'AT_EXTREME_HIGH'  # Reversal likely DOWN
    elif at_extreme_low and at_range_bottom and down_count >= 3:
        mtf_regime = 'AT_EXTREME_LOW'   # Reversal likely UP
    
    confluence = 0
    if up_count + down_count == 8:
        confluence = 100
    elif up_count + down_count >= 6:
        confluence = 75
    elif up_count + down_count >= 4:
        confluence = 50
    else:
        confluence = 25

    # === RECOMMENDED LATTICE GEOMETRY ===
    m15_atr = tfs_data.get('M15', {}).get('atr', 0)
    h1_atr = tfs_data.get('H1', {}).get('atr', 0)

    regime_steps = {
        'STRONG_UPTREND':  {'buy_step': 0.8, 'sell_step': 1.5, 'bias': 'BUY', 'alpha': 0.5},
        'UPTREND':         {'buy_step': 0.9, 'sell_step': 1.3, 'bias': 'BUY', 'alpha': 0.5},
        'STRONG_DOWNTREND':{'buy_step': 1.5, 'sell_step': 0.8, 'bias': 'SELL', 'alpha': 0.5},
        'DOWNTREND':       {'buy_step': 1.3, 'sell_step': 0.9, 'bias': 'SELL', 'alpha': 0.5},
        'AT_EXTREME_HIGH': {'buy_step': 1.5, 'sell_step': 0.7, 'bias': 'SELL', 'alpha': 0.3},
        'AT_EXTREME_LOW':  {'buy_step': 0.7, 'sell_step': 1.5, 'bias': 'BUY', 'alpha': 0.3},
        'TRANSITION':      {'buy_step': 1.0, 'sell_step': 1.0, 'bias': 'NEUTRAL', 'alpha': 0.5},
        'RANGING':         {'buy_step': 0.7, 'sell_step': 0.7, 'bias': 'NEUTRAL', 'alpha': 0.5},
    }

    # === M15/M5 BOUNCE vs BREAKOUT CONFIRMATION ===
    # The HTF tells us WHERE we are (extreme). M15/M5 tells us WHAT is happening (bounce or break).
    m15_data = tfs_data.get('M15', {})
    m5_data = tfs_data.get('M5', {})
    m1_data = tfs_data.get('M1', {})

    bounce_confirmed = False
    breakout_confirmed = False
    reversal_signal = None

    if at_extreme_high or at_range_top:
        # Looking for bounce DOWN (reversal from high)
        # M15: price closed below supertrend or EMA20 crossed below EMA50
        m15_trend = m15_data.get('trend', 'FLAT')
        m5_trend = m5_data.get('trend', 'FLAT')
        m1_trend = m1_data.get('trend', 'FLAT')

        # Bounce confirmed when lower TFs flip DOWN while HTF is at extreme
        if m15_trend == 'DOWN' and m5_trend == 'DOWN':
            bounce_confirmed = True
            reversal_signal = 'BOUNCE_DOWN'
        elif m1_trend == 'DOWN' and m5_trend == 'DOWN':
            # M1 leading the reversal
            bounce_confirmed = True
            reversal_signal = 'BOUNCE_DOWN_EARLY'
        elif m15_trend == 'UP' and m5_trend == 'UP' and m1_trend == 'UP':
            # All lower TFs still pushing up - breakout likely
            breakout_confirmed = True
            reversal_signal = 'BREAKOUT_UP'
        else:
            reversal_signal = 'WAITING'  # Mixed signals, wait for confirmation

    elif at_extreme_low or at_range_bottom:
        # Looking for bounce UP (reversal from low)
        m15_trend = m15_data.get('trend', 'FLAT')
        m5_trend = m5_data.get('trend', 'FLAT')
        m1_trend = m1_data.get('trend', 'FLAT')

        if m15_trend == 'UP' and m5_trend == 'UP':
            bounce_confirmed = True
            reversal_signal = 'BOUNCE_UP'
        elif m1_trend == 'UP' and m5_trend == 'UP':
            bounce_confirmed = True
            reversal_signal = 'BOUNCE_UP_EARLY'
        elif m15_trend == 'DOWN' and m5_trend == 'DOWN' and m1_trend == 'DOWN':
            breakout_confirmed = True
            reversal_signal = 'BREAKOUT_DOWN'
        else:
            reversal_signal = 'WAITING'

    # Adjust geometry based on bounce/breakout confirmation
    if bounce_confirmed:
        # Reversal confirmed - use tight steps on reversal side
        if reversal_signal.startswith('BOUNCE_DOWN'):
            step_config = {'buy_step': 1.5, 'sell_step': 0.5, 'bias': 'SELL', 'alpha': 0.3}
        else:  # BOUNCE_UP
            step_config = {'buy_step': 0.5, 'sell_step': 1.5, 'bias': 'BUY', 'alpha': 0.3}
    elif breakout_confirmed:
        # Breakout confirmed - widen BOTH sides, follow the trend
        if 'UP' in reversal_signal:
            step_config = {'buy_step': 0.8, 'sell_step': 1.2, 'bias': 'BUY', 'alpha': 0.5}
        else:  # BREAKOUT_DOWN
            step_config = {'buy_step': 1.2, 'sell_step': 0.8, 'bias': 'SELL', 'alpha': 0.5}
    else:
        # No confirmation yet - use the HTF-based config, but widen both sides for safety
        step_config = regime_steps.get(mtf_regime, regime_steps['TRANSITION'])
        # At extremes without confirmation, widen both sides (uncertainty premium)
        if mtf_regime in ('AT_EXTREME_HIGH', 'AT_EXTREME_LOW'):
            step_config = {
                'buy_step': max(step_config['buy_step'], 1.2),
                'sell_step': max(step_config['sell_step'], 1.2),
                'bias': 'NEUTRAL',
                'alpha': 0.5,
            }

    result['mtf'] = {
        'regime': mtf_regime,
        'confluence': confluence,
        'up_count': up_count,
        'down_count': down_count,
        'flat_count': flat_count,
        'at_extreme_high': at_extreme_high,
        'at_extreme_low': at_extreme_low,
        'at_range_top': at_range_top,
        'at_range_bottom': at_range_bottom,
        'htf_rsis': {tf: tfs_data[tf].get('rsi') for tf in ['MN','W1','D1','H4'] if tfs_data[tf].get('rsi') is not None},
        # Bounce/breakout confirmation
        'bounce_confirmed': bounce_confirmed,
        'breakout_confirmed': breakout_confirmed,
        'reversal_signal': reversal_signal,
        'm15_trend': m15_data.get('trend', '?'),
        'm5_trend': m5_data.get('trend', '?'),
        'm1_trend': m1_data.get('trend', '?'),
    }
    
    result['recommended_geometry'] = {
        'regime': mtf_regime,
        'bias': step_config['bias'],
        'buy_step_coeff': step_config['buy_step'],
        'sell_step_coeff': step_config['sell_step'],
        'alpha': step_config['alpha'],
        'base_step_atr_m15': round(m15_atr, 5) if m15_atr else None,
        'computed_buy_step': round(m15_atr * step_config['buy_step'], 5) if m15_atr else None,
        'computed_sell_step': round(m15_atr * step_config['sell_step'], 5) if m15_atr else None,
    }
    
    mt5.shutdown()
    return result


def main():
    symbols = ['GBPUSD', 'EURUSD', 'USDJPY', 'NZDUSD', 'AUDUSD', 'USDCAD', 'USDCHF', 'NAS100', 'US30', 'BTCUSD', 'ETHUSD', 'XAUUSD', 'GBPJPY', 'EURJPY', 'XAGUSD']
    
    print(f"{'Symbol':<10} {'MTF Regime':<20} {'Conf':>5} {'Up':>3} {'Dn':>3} {'Fl':>3} {'MN':>4} {'W1':>4} {'D1':>4} {'H4':>4} {'H1':>4} {'M15':>4} {'M5':>4} {'M1':>4} {'Pos%':>5} {'Signal':>16} {'BUY':>6} {'SELL':>6}")
    print("-" * 135)

    all_results = {}
    for sym in symbols:
        result = detect_regime(sym)
        all_results[sym] = result
        mtf = result.get('mtf', {})
        geom = result.get('recommended_geometry', {})
        tfs = result['timeframes']
        d1_data = tfs.get('D1', {})
        pos = d1_data.get('position_in_range', 0.5) if d1_data else 0.5
        trend_str = lambda tf: tfs.get(tf, {}).get('trend', '?')[:3]
        signal = mtf.get('reversal_signal', '?') or '?'
        buy_s = geom.get('buy_step_coeff')
        sell_s = geom.get('sell_step_coeff')
        buy_str = f"{buy_s:.1f}" if buy_s is not None else "?"
        sell_str = f"{sell_s:.1f}" if sell_s is not None else "?"
        pos_pct = f"{(pos or 0.5)*100:.0f}%"
        conf = mtf.get('confluence', 0) or 0
        up = mtf.get('up_count', 0) or 0
        dn = mtf.get('down_count', 0) or 0
        fl = mtf.get('flat_count', 0) or 0
        regime = mtf.get('regime', '?') or '?'
        print(f"{sym:<10} {regime:<20} {conf:>5} {up:>3} {dn:>3} {fl:>3} {trend_str('MN'):>4} {trend_str('W1'):>4} {trend_str('D1'):>4} {trend_str('H4'):>4} {trend_str('H1'):>4} {trend_str('M15'):>4} {trend_str('M5'):>4} {trend_str('M1'):>4} {pos_pct:>5} {signal:>16} {buy_str:>6} {sell_str:>6}")

    # Save results
    script_dir = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(script_dir, '..', 'reports', 'mtf_regime_detection.json')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to {report_path}")
    
    # Detailed geometry for each symbol
    print("\n=== RECOMMENDED LATTICE GEOMETRY ===")
    for sym, result in all_results.items():
        geom = result.get('recommended_geometry', {})
        mtf = result.get('mtf', {})
        m15_atr = geom.get('base_step_atr_m15')
        if m15_atr:
            print(f"\n{sym} - {mtf.get('regime', '?')} (confluence: {mtf.get('confluence', 0)}%, Up:{mtf.get('up_count',0)} Dn:{mtf.get('down_count',0)})")
            print(f"  Bias: {geom.get('bias', '?')}  |  Alpha: {geom.get('alpha', '?')}")
            print(f"  Buy step:  {geom.get('computed_buy_step', '?')} ({geom.get('buy_step_coeff', '?')}x M15 ATR of {m15_atr})")
            print(f"  Sell step: {geom.get('computed_sell_step', '?')} ({geom.get('sell_step_coeff', '?')}x M15 ATR of {m15_atr})")
            if mtf.get('at_extreme_high'):
                print("  WARNING: AT WEEKLY/MONTHLY HIGH - Expect reversal DOWN, tight SELL steps")
            if mtf.get('at_extreme_low'):
                print("  WARNING: AT WEEKLY/MONTHLY LOW - Expect reversal UP, tight BUY steps")


if __name__ == '__main__':
    main()
