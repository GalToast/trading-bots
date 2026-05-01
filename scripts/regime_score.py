#!/usr/bin/env python3
"""Rolling regime score — lightweight version for live runner integration.

Computes a regime score [-1, +1] for each coin every cycle:
  +1 = strong oscillation (mean-reversion works)
   0 = ambiguous / transition
  -1 = strong trend (mean-reversion dies)

Metrics:
  1. ATR expansion rate — current ATR vs rolling average
  2. Level penetration coherence — how often price returns to previous levels
  3. Temporal stability — PnL consistency across windows

Usage (from runner):
  from regime_score import RollingRegimeScore
  regime = RollingRegimeScore(window=20)
  score = regime.update(candles)  # returns score in [-1, +1]
"""


class RollingRegimeScore:
    """Lightweight rolling regime score for live runner integration."""
    
    def __init__(self, window=20, atr_period=14):
        self.window = window
        self.atr_period = atr_period
        self.atr_values = []
        self.coherence_scores = []
    
    def _compute_atr(self, candles):
        """Compute ATR for the last N candles."""
        if len(candles) < self.atr_period + 1:
            return None
        trs = []
        for i in range(-self.atr_period, 0):
            c = candles[i]
            p = candles[i - 1]
            h = float(c["high"])
            l = float(c["low"])
            pc = float(p["close"])
            tr = max(
                h - l,
                abs(h - pc),
                abs(l - pc),
            )
            trs.append(tr)
        return sum(trs) / len(trs)
    
    def _compute_coherence(self, candles):
        """Compute level penetration coherence.
        
        How often does price return to levels it visited recently?
        High coherence = oscillation. Low = trend.
        """
        if len(candles) < self.window * 2:
            return 0.5  # neutral
        
        recent = candles[-self.window:]
        prior = candles[-self.window * 2:-self.window]

        # Count how many recent price levels overlap with prior levels
        prior_mids = [(float(c["high"]) + float(c["low"])) / 2 for c in prior]
        recent_mids = [(float(c["high"]) + float(c["low"])) / 2 for c in recent]

        if not prior_mids or not recent_mids:
            return 0.5

        # Simple overlap: count recent mids within 0.5% of any prior mid
        price_range = max(prior_mids) - min(prior_mids)
        if price_range == 0:
            return 0.5
        
        threshold = price_range * 0.05  # 5% of range
        overlaps = 0
        for rm in recent_mids:
            for pm in prior_mids:
                if abs(rm - pm) < threshold:
                    overlaps += 1
                    break
        
        return overlaps / len(recent_mids)
    
    def _compute_stability(self, candles):
        """Compute temporal stability.
        
        Split recent candles into windows and check PnL consistency.
        """
        if len(candles) < self.window * 2:
            return 0.0
        
        # Split into 4 windows
        window_size = len(candles) // 4
        if window_size < 5:
            return 0.0
        
        windows = []
        for i in range(4):
            start = i * window_size
            end = start + window_size
            w = candles[start:end]
            if w:
                # Simple mean-reversion PnL: buy at open, sell at close if down bar
                pnl = 0
                for c in w:
                    o = float(c["open"])
                    cl = float(c["close"])
                    if cl < o:
                        pnl += (o - cl) / o
                    else:
                        pnl -= (cl - o) / o
                windows.append(pnl)
        
        if len(windows) < 2:
            return 0.0
        
        # Stability: low variance in window PnLs = high stability
        mean_pnl = sum(windows) / len(windows)
        variance = sum((w - mean_pnl) ** 2 for w in windows) / len(windows)
        std = variance ** 0.5
        
        # Map std to [-1, 1]: low std = +1, high std = -1
        # Normalize by mean absolute return
        mean_abs = sum(abs(w) for w in windows) / len(windows)
        if mean_abs == 0:
            return 0.0
        
        cv = std / mean_abs  # coefficient of variation
        # Map CV: 0 -> +1, 2+ -> -1
        stability = 1.0 - min(2.0, cv)
        return stability
    
    def update(self, candles):
        """Update regime score with new candles.
        
        Args:
            candles: list of dicts with keys: time, open, high, low, close, volume
        
        Returns:
            dict with score, atr, coherence, stability, regime
        """
        atr = self._compute_atr(candles)
        coherence = self._compute_coherence(candles)
        stability = self._compute_stability(candles)
        
        # ATR component: if we have ATR data, check expansion
        atr_score = 0.0
        if atr is not None:
            self.atr_values.append(atr)
            if len(self.atr_values) > self.window:
                self.atr_values = self.atr_values[-self.window:]
            
            if len(self.atr_values) >= 5:
                avg_atr = sum(self.atr_values[:-1]) / (len(self.atr_values) - 1)
                current_atr = self.atr_values[-1]
                if avg_atr > 0:
                    ratio = current_atr / avg_atr
                    # ratio > 1.5 = trend (-1), ratio < 0.5 = oscillation (+1)
                    atr_score = 1.0 - 2.0 * min(1.0, max(0.0, (ratio - 0.5) / 1.0))
        
        # Coherence: already in [0, 1] -> map to [-1, 1]
        coherence_norm = 2.0 * coherence - 1.0
        
        # Stability: already in [-1, 1]
        
        # Composite
        components = [c for c in [atr_score, coherence_norm, stability] if c is not None]
        if not components:
            score = 0.0
        else:
            score = sum(components) / len(components)
        
        # Classify regime
        if score > 0.25:
            regime = "oscillation"
        elif score < -0.25:
            regime = "trend"
        else:
            regime = "transition"
        
        result = {
            "score": round(score, 4),
            "atr_score": round(atr_score, 4),
            "coherence": round(coherence, 4),
            "coherence_norm": round(coherence_norm, 4),
            "stability": round(stability, 4),
            "regime": regime,
            "atr": round(atr, 6) if atr else None,
        }
        
        return result
