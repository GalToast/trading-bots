from __future__ import annotations


def get_price_edge_signal(
    *,
    symbol,
    diagnostics=None,
    price_allow_exotics,
    is_exotic,
    get_bars,
    calc_atr,
    mt5_module,
    price_breakout_min_confidence,
    price_pullback_min_confidence,
    price_rejection_min_confidence,
    price_pass_confidence,
):
    """
    Price-only thesis lane for this experiment.
    Returns: (signal, confidence, atr, thesis, signal_type)
    """
    try:
        if diagnostics is not None:
            diagnostics["price_scanned"] = diagnostics.get("price_scanned", 0) + 1

        if not price_allow_exotics and is_exotic(symbol):
            if diagnostics is not None:
                diagnostics["price_fail_exotic"] = diagnostics.get("price_fail_exotic", 0) + 1
            return None, 0.0, 0.0, None, None

        bars_m1 = get_bars(symbol, mt5_module.TIMEFRAME_M1, 20)
        if len(bars_m1) < 8:
            if diagnostics is not None:
                diagnostics["price_fail_m1_bars"] = diagnostics.get("price_fail_m1_bars", 0) + 1
            return None, 0.0, 0.0, None, None

        bars_m5 = get_bars(symbol, mt5_module.TIMEFRAME_M5, 60)
        if len(bars_m5) < 25:
            if diagnostics is not None:
                diagnostics["price_fail_bars"] = diagnostics.get("price_fail_bars", 0) + 1
            return None, 0.0, 0.0, None, None

        bars_m15 = get_bars(symbol, mt5_module.TIMEFRAME_M15, 40)
        if len(bars_m15) < 20:
            if diagnostics is not None:
                diagnostics["price_fail_htf_bars"] = diagnostics.get("price_fail_htf_bars", 0) + 1
            return None, 0.0, 0.0, None, None

        m1_closes = [b["c"] for b in bars_m1]
        m1_highs = [b["h"] for b in bars_m1]
        m1_lows = [b["l"] for b in bars_m1]
        m1_opens = [b["o"] for b in bars_m1]
        closes = [b["c"] for b in bars_m5]
        highs = [b["h"] for b in bars_m5]
        lows = [b["l"] for b in bars_m5]
        atr = calc_atr(bars_m5, 14) if len(bars_m5) >= 15 else 0.0
        if atr <= 0:
            if diagnostics is not None:
                diagnostics["price_fail_atr"] = diagnostics.get("price_fail_atr", 0) + 1
            return None, 0.0, 0.0, None, None

        price = closes[-1]
        curr_open = bars_m5[-1]["o"]
        curr_high = highs[-1]
        curr_low = lows[-1]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        current_range = max(0.0, curr_high - curr_low)
        if current_range <= 0:
            if diagnostics is not None:
                diagnostics["price_fail_flat_bar"] = diagnostics.get("price_fail_flat_bar", 0) + 1
            return None, 0.0, 0.0, None, None

        swing_period = 20
        swing_high = max(highs[-swing_period:-1])
        swing_low = min(lows[-swing_period:-1])
        recent_ranges = [max(0.0, h - l) for h, l in zip(highs[-15:-1], lows[-15:-1])]
        median_range = sorted(recent_ranges)[len(recent_ranges) // 2] if recent_ranges else atr
        body = abs(price - curr_open)
        close_pos = (price - curr_low) / current_range
        close_pos_short = 1.0 - close_pos
        upper_wick = curr_high - max(curr_open, price)
        lower_wick = min(curr_open, price) - curr_low
        range_expansion = current_range >= max(atr * 0.9, median_range * 1.1)
        breakout_buffer = atr * 0.08
        prev_mid = (prev_high + prev_low) / 2.0
        m1_last_high = max(m1_highs[-3:])
        m1_last_low = min(m1_lows[-3:])
        m1_last_range = max(0.0, m1_last_high - m1_last_low)
        m1_up_impulse = (
            m1_closes[-1] > m1_closes[-2] > m1_closes[-4]
            and m1_lows[-1] >= m1_lows[-2] >= m1_lows[-4]
            and m1_closes[-1] >= m1_last_low + (m1_last_range * 0.65 if m1_last_range > 0 else 0.0)
        )
        m1_down_impulse = (
            m1_closes[-1] < m1_closes[-2] < m1_closes[-4]
            and m1_highs[-1] <= m1_highs[-2] <= m1_highs[-4]
            and m1_closes[-1] <= m1_last_high - (m1_last_range * 0.65 if m1_last_range > 0 else 0.0)
        )
        m1_reclaim_up = m1_closes[-1] > max(m1_opens[-1], m1_closes[-2], prev_close)
        m1_reclaim_down = m1_closes[-1] < min(m1_opens[-1], m1_closes[-2], prev_close)

        uptrend = highs[-3] > highs[-6] and lows[-3] > lows[-6]
        downtrend = highs[-3] < highs[-6] and lows[-3] < lows[-6]
        htf_closes = [b["c"] for b in bars_m15]
        htf_highs = [b["h"] for b in bars_m15]
        htf_lows = [b["l"] for b in bars_m15]
        htf_uptrend = htf_highs[-2] > htf_highs[-5] and htf_lows[-2] > htf_lows[-5]
        htf_downtrend = htf_highs[-2] < htf_highs[-5] and htf_lows[-2] < htf_lows[-5]
        htf_swing_high = max(htf_highs[-12:-1])
        htf_swing_low = min(htf_lows[-12:-1])
        htf_breakout_above = htf_closes[-1] >= htf_swing_high - breakout_buffer
        htf_breakout_below = htf_closes[-1] <= htf_swing_low + breakout_buffer

        breakout_above = prev_close <= swing_high and price >= swing_high + breakout_buffer
        breakout_below = prev_close >= swing_low and price <= swing_low - breakout_buffer
        bullish_rejection = (
            curr_low <= swing_low + breakout_buffer
            and price > swing_low
            and close_pos >= 0.68
            and lower_wick >= max(body * 1.4, current_range * 0.30)
            and closes[-2] <= swing_low + breakout_buffer
            and price > prev_mid
        )
        bearish_rejection = (
            curr_high >= swing_high - breakout_buffer
            and price < swing_high
            and close_pos_short >= 0.68
            and upper_wick >= max(body * 1.4, current_range * 0.30)
            and closes[-2] >= swing_high - breakout_buffer
            and price < prev_mid
        )
        bullish_pullback_resume = (
            uptrend
            and htf_uptrend
            and closes[-2] < closes[-3]
            and prev_low > swing_low
            and price > prev_high + breakout_buffer
            and close_pos >= 0.62
            and current_range >= atr * 0.75
        )
        bearish_pullback_resume = (
            downtrend
            and htf_downtrend
            and closes[-2] > closes[-3]
            and prev_high < swing_high
            and price < prev_low - breakout_buffer
            and close_pos_short >= 0.62
            and current_range >= atr * 0.75
        )

        signal = None
        confidence = 0.0
        thesis = None
        signal_type = None

        def score_to_confidence(base_confidence, score, required_score):
            if score < required_score:
                return 0.0
            extra = min(0.40, max(0.0, score - required_score) * 0.08)
            return min(0.96, base_confidence + extra)

        breakout_buy_score = 0.0
        breakout_sell_score = 0.0
        pullback_buy_score = 0.0
        pullback_sell_score = 0.0
        rejection_buy_score = 0.0
        rejection_sell_score = 0.0

        if breakout_above:
            breakout_buy_score += 1.0
            if range_expansion:
                breakout_buy_score += 1.0
            if close_pos >= 0.82:
                breakout_buy_score += 1.0
            if htf_uptrend:
                breakout_buy_score += 1.0
            if htf_breakout_above:
                breakout_buy_score += 1.0
            if m1_up_impulse:
                breakout_buy_score += 1.0
            if m1_reclaim_up:
                breakout_buy_score += 1.0

        if breakout_below:
            breakout_sell_score += 1.0
            if range_expansion:
                breakout_sell_score += 1.0
            if close_pos_short >= 0.82:
                breakout_sell_score += 1.0
            if htf_downtrend:
                breakout_sell_score += 1.0
            if htf_breakout_below:
                breakout_sell_score += 1.0
            if m1_down_impulse:
                breakout_sell_score += 1.0
            if m1_reclaim_down:
                breakout_sell_score += 1.0

        if bullish_pullback_resume:
            pullback_buy_score += 1.0
            if uptrend:
                pullback_buy_score += 1.0
            if htf_uptrend:
                pullback_buy_score += 1.0
            if close_pos >= 0.66:
                pullback_buy_score += 1.0
            if current_range >= atr * 0.90:
                pullback_buy_score += 1.0
            if m1_up_impulse:
                pullback_buy_score += 1.0
            if m1_reclaim_up:
                pullback_buy_score += 1.0

        if bearish_pullback_resume:
            pullback_sell_score += 1.0
            if downtrend:
                pullback_sell_score += 1.0
            if htf_downtrend:
                pullback_sell_score += 1.0
            if close_pos_short >= 0.66:
                pullback_sell_score += 1.0
            if current_range >= atr * 0.90:
                pullback_sell_score += 1.0
            if m1_down_impulse:
                pullback_sell_score += 1.0
            if m1_reclaim_down:
                pullback_sell_score += 1.0

        if bullish_rejection and not htf_downtrend:
            rejection_buy_score += 1.0
            if close_pos >= 0.74:
                rejection_buy_score += 1.0
            if lower_wick >= max(body * 1.8, current_range * 0.36):
                rejection_buy_score += 1.0
            if current_range >= atr * 0.75:
                rejection_buy_score += 1.0
            if m1_up_impulse:
                rejection_buy_score += 1.0
            if m1_reclaim_up:
                rejection_buy_score += 1.0
            if not downtrend:
                rejection_buy_score += 0.5

        if bearish_rejection and not htf_uptrend:
            rejection_sell_score += 1.0
            if close_pos_short >= 0.74:
                rejection_sell_score += 1.0
            if upper_wick >= max(body * 1.8, current_range * 0.36):
                rejection_sell_score += 1.0
            if current_range >= atr * 0.75:
                rejection_sell_score += 1.0
            if m1_down_impulse:
                rejection_sell_score += 1.0
            if m1_reclaim_down:
                rejection_sell_score += 1.0
            if not uptrend:
                rejection_sell_score += 0.5

        breakout_buy_conf = score_to_confidence(price_breakout_min_confidence, breakout_buy_score, 3.0)
        breakout_sell_conf = score_to_confidence(price_breakout_min_confidence, breakout_sell_score, 3.0)
        pullback_buy_conf = score_to_confidence(price_pullback_min_confidence, pullback_buy_score, 3.0)
        pullback_sell_conf = score_to_confidence(price_pullback_min_confidence, pullback_sell_score, 3.0)
        rejection_buy_conf = score_to_confidence(price_rejection_min_confidence, rejection_buy_score, 3.5)
        rejection_sell_conf = score_to_confidence(price_rejection_min_confidence, rejection_sell_score, 3.5)

        if breakout_buy_conf >= price_pass_confidence:
            signal = "BUY"
            confidence = breakout_buy_conf
            thesis = "breakout_continuation"
            signal_type = "breakout_hold_above_high"
        elif breakout_sell_conf >= price_pass_confidence:
            signal = "SELL"
            confidence = breakout_sell_conf
            thesis = "breakout_continuation"
            signal_type = "breakout_hold_below_low"
        elif pullback_buy_conf >= price_pass_confidence:
            signal = "BUY"
            confidence = pullback_buy_conf
            thesis = "pullback_continuation"
            signal_type = "pullback_to_structure_hold"
        elif pullback_sell_conf >= price_pass_confidence:
            signal = "SELL"
            confidence = pullback_sell_conf
            thesis = "pullback_continuation"
            signal_type = "pullback_from_structure_fail"
        elif rejection_buy_conf >= price_pass_confidence:
            signal = "BUY"
            confidence = rejection_buy_conf
            thesis = "range_rejection"
            signal_type = "range_low_reject"
        elif rejection_sell_conf >= price_pass_confidence:
            signal = "SELL"
            confidence = rejection_sell_conf
            thesis = "range_rejection"
            signal_type = "range_high_reject"

        best_price_conf = max(
            breakout_buy_conf,
            breakout_sell_conf,
            pullback_buy_conf,
            pullback_sell_conf,
            rejection_buy_conf,
            rejection_sell_conf,
        )
        best_price_score = max(
            breakout_buy_score,
            breakout_sell_score,
            pullback_buy_score,
            pullback_sell_score,
            rejection_buy_score,
            rejection_sell_score,
        )
        best_price_signal_type = None
        if best_price_conf == breakout_buy_conf and breakout_buy_conf > 0:
            best_price_signal_type = "breakout_hold_above_high"
        elif best_price_conf == breakout_sell_conf and breakout_sell_conf > 0:
            best_price_signal_type = "breakout_hold_below_low"
        elif best_price_conf == pullback_buy_conf and pullback_buy_conf > 0:
            best_price_signal_type = "pullback_to_structure_hold"
        elif best_price_conf == pullback_sell_conf and pullback_sell_conf > 0:
            best_price_signal_type = "pullback_from_structure_fail"
        elif best_price_conf == rejection_buy_conf and rejection_buy_conf > 0:
            best_price_signal_type = "range_low_reject"
        elif best_price_conf == rejection_sell_conf and rejection_sell_conf > 0:
            best_price_signal_type = "range_high_reject"
        elif best_price_score == breakout_buy_score and breakout_buy_score > 0:
            best_price_signal_type = "breakout_hold_above_high"
        elif best_price_score == breakout_sell_score and breakout_sell_score > 0:
            best_price_signal_type = "breakout_hold_below_low"
        elif best_price_score == pullback_buy_score and pullback_buy_score > 0:
            best_price_signal_type = "pullback_to_structure_hold"
        elif best_price_score == pullback_sell_score and pullback_sell_score > 0:
            best_price_signal_type = "pullback_from_structure_fail"
        elif best_price_score == rejection_buy_score and rejection_buy_score > 0:
            best_price_signal_type = "range_low_reject"
        elif best_price_score == rejection_sell_score and rejection_sell_score > 0:
            best_price_signal_type = "range_high_reject"

        if diagnostics is not None and best_price_conf > float(diagnostics.get("price_best_confidence", 0.0) or 0.0):
            diagnostics["price_best_confidence"] = float(best_price_conf)
            diagnostics["price_best_symbol"] = symbol
            diagnostics["price_best_signal_type"] = best_price_signal_type or "-"
        if diagnostics is not None and best_price_score > float(diagnostics.get("price_best_score", 0.0) or 0.0):
            diagnostics["price_best_score"] = float(best_price_score)
            diagnostics["price_best_score_symbol"] = symbol
            diagnostics["price_best_score_signal_type"] = best_price_signal_type or "-"

        if signal and diagnostics is not None:
            diagnostics["price_signal"] = diagnostics.get("price_signal", 0) + 1
            diagnostics["price_conf"] = diagnostics.get("price_conf", 0) + confidence
            thesis_key = f"price_{thesis}"
            diagnostics[thesis_key] = diagnostics.get(thesis_key, 0) + 1
        elif diagnostics is not None:
            diagnostics["price_near_miss"] = diagnostics.get("price_near_miss", 0) + int(
                best_price_conf >= (price_pass_confidence - 0.04)
            )

        return signal, confidence, atr, thesis, signal_type
    except Exception:
        return None, 0.0, 0.0, None, None
