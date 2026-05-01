"""
MT5 Trading Brain - Self-Learning System
Shared between V9 (entries) and Exit Manager (exits)
Tracks performance per symbol and adapts behavior
"""
import json
import os
from collections.abc import Mapping
from datetime import datetime, timedelta

BRAIN_FILE = os.path.join(os.path.dirname(__file__), "brain.json")
VALID_MODES = ("SNIPER", "SHOTGUN", "MACHINE_GUN", "UNKNOWN")

class TradingBrain:
    def __init__(self):
        self.data = self._load()
    
    def _load(self):
        if os.path.exists(BRAIN_FILE):
            try:
                with open(BRAIN_FILE, 'r') as f:
                    return self._sanitize(json.load(f))
            except (json.JSONDecodeError, ValueError) as e:
                # Corrupted brain.json — save a backup before resetting
                backup_path = BRAIN_FILE + ".corrupted"
                try:
                    import shutil
                    shutil.copy2(BRAIN_FILE, backup_path)
                    print(f"[BRAIN] Corrupted state saved to {backup_path}: {e}", flush=True)
                except OSError:
                    print(f"[BRAIN] Failed to backup corrupted state: {e}", flush=True)
                return self._empty()
            except OSError as e:
                print(f"[BRAIN] Failed to read brain.json: {e}", flush=True)
                return self._empty()
        return self._empty()

    def _empty(self):
        return {"symbols": {}, "global": {"total_trades": 0, "total_wins": 0, "total_losses": 0}}

    def _sanitize_symbol(self, info):
        cleaned = {
            "trades": max(0, int(info.get("trades", 0))),
            "wins": max(0, int(info.get("wins", 0))),
            "losses": max(0, int(info.get("losses", 0))),
            "win_rate": 0.0,
            "total_pnl": float(info.get("total_pnl", 0.0)),
            "avg_profit": max(0.0, float(info.get("avg_profit", 0.0))),
            "avg_loss": max(0.0, float(info.get("avg_loss", 0.0))),
            "best_mode": "UNKNOWN",
            "mode_wins": {},
            "confidence_adjust": float(info.get("confidence_adjust", 0.0)),
            "lot_multiplier": float(info.get("lot_multiplier", 1.0)),
            "cooldown_until": info.get("cooldown_until"),
            "last_trade": info.get("last_trade"),
        }

        mode_wins = info.get("mode_wins", {})
        if isinstance(mode_wins, dict):
            for mode, stats in mode_wins.items():
                if mode not in VALID_MODES or not isinstance(stats, dict):
                    continue
                total = max(0, int(stats.get("total", 0)))
                wins = min(total, max(0, int(stats.get("wins", 0))))
                cleaned["mode_wins"][mode] = {"wins": wins, "total": total}

        cleaned["trades"] = max(cleaned["trades"], cleaned["wins"] + cleaned["losses"])
        cleaned["wins"] = min(cleaned["trades"], cleaned["wins"])
        cleaned["losses"] = min(cleaned["trades"] - cleaned["wins"], cleaned["losses"])
        cleaned["win_rate"] = cleaned["wins"] / cleaned["trades"] if cleaned["trades"] > 0 else 0.0
        cleaned["confidence_adjust"] = max(-0.1, min(0.3, cleaned["confidence_adjust"]))
        cleaned["lot_multiplier"] = max(0.3, min(2.0, cleaned["lot_multiplier"]))

        best_mode = None
        best_wr = -1.0
        for mode, stats in cleaned["mode_wins"].items():
            if stats["total"] < 3:
                continue
            wr = stats["wins"] / stats["total"] if stats["total"] else 0.0
            if wr > best_wr:
                best_wr = wr
                best_mode = mode
        cleaned["best_mode"] = best_mode or info.get("best_mode") if info.get("best_mode") in VALID_MODES else "UNKNOWN"
        if cleaned["best_mode"] not in VALID_MODES:
            cleaned["best_mode"] = "UNKNOWN"
        return cleaned

    def _sanitize(self, data):
        symbols = {}
        for symbol, info in (data.get("symbols") or {}).items():
            if isinstance(info, dict):
                symbols[symbol] = self._sanitize_symbol(info)

        total_trades = sum(info["trades"] for info in symbols.values())
        total_wins = sum(info["wins"] for info in symbols.values())
        total_losses = sum(info["losses"] for info in symbols.values())

        return {
            "symbols": symbols,
            "global": {
                "total_trades": total_trades,
                "total_wins": total_wins,
                "total_losses": total_losses,
            },
        }
    
    def save(self):
        """Persist brain state to disk using atomic write (temp + rename) to prevent corruption."""
        tmp_path = BRAIN_FILE + ".tmp"
        try:
            with open(tmp_path, 'w') as f:
                json.dump(self.data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, BRAIN_FILE)
        except OSError as e:
            print(f"[BRAIN] Failed to save state: {e}", flush=True)
            # Clean up temp file if it exists
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    
    def record_exit(self, symbol, pnl, mode, hold_seconds, failure_reason=None, entry_price=None, current_price=None):
        """Called after every close. failure_reason: STOP_HIT, TAKE_PROFIT, TIMEOUT, STRESS_TRIM, MARGIN_CALL, MANUAL, WHIPSAW"""
        if symbol not in self.data["symbols"]:
            self.data["symbols"][symbol] = {
                "trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "total_pnl": 0.0,
                "avg_profit": 0.0, "avg_loss": 0.0,
                "best_mode": mode, "mode_wins": {},
                "confidence_adjust": 0.0, "lot_multiplier": 1.0,
                "cooldown_until": None, "last_trade": None,
                "consecutive_losses": 0, "consecutive_wins": 0,
                "last_direction": None, "direction_flips": 0,
                "failure_reasons": {}, "entry_price": None,
                "atr": None, "spread_at_entry": None,
                "volatility_regime": None
            }
        
        sym = self.data["symbols"][symbol]
        
        # Auto-detect failure reason if not provided
        if not failure_reason:
            if pnl > 0:
                # Winner - could be TP or manual
                if hold_seconds < 30:
                    failure_reason = "QUICK_WIN"  # Fast profit - possible whipsaw
                elif hold_seconds > 3600:
                    failure_reason = "SWING_WIN"
                else:
                    failure_reason = "TAKE_PROFIT"
            else:
                # Loser - determine type
                abs_pnl = abs(pnl)
                if hold_seconds < 20:
                    failure_reason = "WHIPSAW"  # Very fast loss
                elif hold_seconds < 60:
                    failure_reason = "QUICK_LOSS"  # Fast loss
                elif abs_pnl < 5:
                    failure_reason = "TIGHT_STOP"
                else:
                    failure_reason = "STOP_HIT"
        
        # Track consecutive wins/losses
        if pnl > 0:
            sym["consecutive_wins"] = sym.get("consecutive_wins", 0) + 1
            sym["consecutive_losses"] = 0
            # Reset failure flags on win
            sym["needs_wider_stops"] = False
            sym["needs_longer_cooldown"] = False
            sym["whipsaw_prone"] = False
        else:
            sym["consecutive_losses"] = sym.get("consecutive_losses", 0) + 1
            sym["consecutive_wins"] = 0
            # Set failure flags based on loss type
            if failure_reason == "WHIPSAW":
                sym["whipsaw_prone"] = True
            if failure_reason in ("TIGHT_STOP", "STOP_HIT") and hold_seconds < 120:
                sym["needs_wider_stops"] = True
            if sym.get("consecutive_losses", 0) >= 3:
                sym["needs_longer_cooldown"] = True
        
        # Track failure reasons
            if "failure_reasons" not in sym:
                sym["failure_reasons"] = {}
            if failure_reason not in sym["failure_reasons"]:
                sym["failure_reasons"][failure_reason] = 0
            sym["failure_reasons"][failure_reason] += 1
        
        # After 3+ consecutive losses, try flipping direction
        if sym.get("consecutive_losses", 0) >= 3:
            current_dir = sym.get("last_direction")
            if current_dir and (entry_price and current_price):
                # Check if we were right direction by checking price movement
                if current_dir == "BUY" and current_price < entry_price:
                    sym["direction_flips"] = sym.get("direction_flips", 0) + 1
                elif current_dir == "SELL" and current_price > entry_price:
                    sym["direction_flips"] = sym.get("direction_flips", 0) + 1
        sym["trades"] += 1
        sym["total_pnl"] += pnl
        sym["last_trade"] = datetime.now().isoformat()
        
        # Track mode performance
        if mode not in sym["mode_wins"]:
            sym["mode_wins"][mode] = {"wins": 0, "total": 0}
        sym["mode_wins"][mode]["total"] += 1
        
        if pnl > 0:
            sym["wins"] += 1
            sym["mode_wins"][mode]["wins"] += 1
            # Update avg profit
            sym["avg_profit"] = (sym["avg_profit"] * (sym["wins"] - 1) + pnl) / sym["wins"] if sym["wins"] > 0 else pnl
        else:
            sym["losses"] += 1
            # Update avg loss
            sym["avg_loss"] = (sym["avg_loss"] * (sym["losses"] - 1) + abs(pnl)) / sym["losses"] if sym["losses"] > 0 else abs(pnl)
        
        # Calculate win rate
        sym["win_rate"] = sym["wins"] / sym["trades"] if sym["trades"] > 0 else 0.0
        
        # Find best mode
        best_mode = None
        best_wr = 0
        for m, stats in sym["mode_wins"].items():
            if stats["total"] >= 3:  # Need minimum 3 trades to judge
                wr = stats["wins"] / stats["total"]
                if wr > best_wr:
                    best_wr = wr
                    best_mode = m
        if best_mode:
            sym["best_mode"] = best_mode
        
        # Update global stats
        self.data["global"]["total_trades"] += 1
        if pnl > 0:
            self.data["global"]["total_wins"] += 1
        else:
            self.data["global"]["total_losses"] += 1
        
        # Apply adaptation rules
        self._adapt(symbol, sym)
        self.save()
    
    def _adapt(self, symbol, sym):
        """Adjust parameters based on performance AND failure analysis"""
        
        # === FAILURE REASON ANALYSIS ===
        failure_reasons = sym.get("failure_reasons", {})
        total_losses = sym.get("losses", 0)
        
        # If STOP_HIT is dominant (>50% of losses), stops are too tight
        stop_hits = failure_reasons.get("STOP_HIT", 0)
        if total_losses > 0 and stop_hits / total_losses > 0.5:
            sym["stop_too_tight"] = True  # Signal to widen stops
        else:
            sym["stop_too_tight"] = False
        
        # If WHIPSAW is dominant, we're overtrading - need longer cooldowns
        whipsaws = failure_reasons.get("WHIPSAW", 0)
        if total_losses > 0 and whipsaws / total_losses > 0.4:
            sym["whipsaw_prone"] = True
        else:
            sym["whipsaw_prone"] = False
        
        # === CONSECUTIVE LOSS ESCALATION ===
        consec_losses = sym.get("consecutive_losses", 0)
        if consec_losses >= 5:
            # Another agent: Reduced 2-hour cooldown to 15 mins for 10x competition
            sym["cooldown_until"] = (datetime.now() + timedelta(minutes=15)).isoformat()
            sym["lot_multiplier"] = max(0.25, sym.get("lot_multiplier", 1.0) - 0.2)
        elif consec_losses >= 3:
            # Another agent: Reduced 1-hour cooldown to 5 mins
            sym["cooldown_until"] = (datetime.now() + timedelta(minutes=5)).isoformat()
            sym["lot_multiplier"] = max(0.5, sym.get("lot_multiplier", 1.0) - 0.1)

        # === TRADING ADAPTATION ===
        # Another agent: Increased P/L trigger to -$200 to prevent blocking 10x lot stop hits
        if sym["total_pnl"] < -200.0 and not sym.get("cooldown_until"):
            sym["cooldown_until"] = (datetime.now() + timedelta(minutes=5)).isoformat()        # Clear cooldown if profitable
        elif sym["total_pnl"] > 0:
            sym["cooldown_until"] = None
            sym["consecutive_losses"] = 0  # Reset streak on win
        
        # Confidence adjustment — cap impact so mode floor still matters
        # Max adjustment: +0.15 (was +0.30, too aggressive and blocked all entries)
        if sym["win_rate"] < 0.40 and sym["trades"] >= 5:
            sym["confidence_adjust"] = min(0.15, sym.get("confidence_adjust", 0) + 0.03)  # Harder to enter
        elif sym["win_rate"] > 0.60 and sym["trades"] >= 5:
            sym["confidence_adjust"] = max(-0.1, sym.get("confidence_adjust", 0) - 0.02)  # Easier to enter
        
        # Lot size adjustment (unless in consec loss mode)
        # === HOT STREAK ACCELERATION (qwen-main 2026-04-13) ===
        # Symbols on a verified winning streak scale up faster than the baseline +0.1/cycle.
        # This compounds gains when a symbol is truly on fire, countering the asymmetry
        # where losses hit faster than wins recover.
        consec_wins = sym.get("consecutive_wins", 0)
        if sym["win_rate"] > 0.70 and consec_wins >= 3 and sym["trades"] >= 5:
            # Hot streak: 70%+ WR, 3+ consecutive wins — aggressive ramp
            sym["lot_multiplier"] = min(2.0, sym.get("lot_multiplier", 1.0) + 0.15)
        elif sym["win_rate"] > 0.65 and consec_wins >= 5 and sym["trades"] >= 8:
            # Sustained fire: 65%+ WR, 5+ consecutive wins — super ramp
            sym["lot_multiplier"] = min(2.0, sym.get("lot_multiplier", 1.0) + 0.20)
        elif sym["win_rate"] > 0.60 and sym["trades"] >= 10:
            # Baseline: 60%+ WR, 10+ trades — standard ramp
            sym["lot_multiplier"] = min(2.0, sym.get("lot_multiplier", 1.0) + 0.1)  # Increase size
        elif sym["win_rate"] < 0.35 and sym["trades"] >= 5 and consec_losses < 3:
            sym["lot_multiplier"] = max(0.3, sym.get("lot_multiplier", 1.0) - 0.1)  # Decrease size
        
        # === FAILURE MODE FLAG ===
        sym["needs_wider_stops"] = sym.get("stop_too_tight", False)
        sym["needs_longer_cooldown"] = sym.get("whipsaw_prone", False)
    
    def get_entry_params(self, symbol, base_confidence, base_lot):
        """Called by V9 before entry - returns adjusted params"""
        if symbol not in self.data["symbols"]:
            return {
                "allowed": True,
                "confidence_threshold": base_confidence,
                "lot_size": base_lot,
                "recommended_mode": None,
                "reason": "New symbol - no data"
            }
        
        sym = self.data["symbols"][symbol]
        if not isinstance(sym, Mapping):
            sym = self._sanitize_symbol({})
            self.data["symbols"][symbol] = sym
        
        # Check cooldown
        if sym["cooldown_until"]:
            cooldown_time = datetime.fromisoformat(sym["cooldown_until"])
            now = datetime.now()
            if now < cooldown_time:
                remaining_seconds = max(0, int((cooldown_time - now).total_seconds()))
                if remaining_seconds < 60:
                    remaining_text = "<1m"
                else:
                    remaining_text = f"{(remaining_seconds + 59) // 60}m"
                return {
                    "allowed": False,
                    "confidence_threshold": 999,
                    "lot_size": 0,
                    "recommended_mode": None,
                    "reason": f"Cooldown ({remaining_text} remaining) - P/L: ${sym['total_pnl']:+.2f}"
                }
            else:
                sym["cooldown_until"] = None  # Cooldown expired
                self.save()
        
        # Adjust confidence threshold
        adjusted_confidence = base_confidence + sym["confidence_adjust"]
        
        # Adjust lot size
        adjusted_lot = base_lot * sym["lot_multiplier"]
        
        # Get recommended mode
        recommended_mode = sym.get("best_mode")
        
        # Check failure mode flags
        consec_losses = sym.get("consecutive_losses", 0)
        direction_flips = sym.get("direction_flips", 0)
        
        # Build failure reason string
        failure_summary = []
        if sym.get("needs_wider_stops"):
            failure_summary.append("WIDER_STOPS")
        if sym.get("needs_longer_cooldown"):
            failure_summary.append("LONG_COOLDOWN")
        if consec_losses >= 3:
            failure_summary.append(f"CONSEC_LOSS:{consec_losses}")
        
        return {
            "allowed": True,
            "confidence_threshold": adjusted_confidence,
            "lot_size": adjusted_lot,
            "recommended_mode": recommended_mode,
            "consecutive_losses": consec_losses,
            "direction_flips": direction_flips,
            "failure_flags": failure_summary,
            "needs_wider_stops": sym.get("needs_wider_stops", False),
            "needs_longer_cooldown": sym.get("needs_longer_cooldown", False),
            "reason": f"WR:{sym['win_rate']:.0%} Trades:{sym['trades']} P/L:${sym['total_pnl']:+.2f}"
        }
    
    def get_symbol_data(self, symbol):
        """Return raw symbol data for bot compatibility"""
        return self.data["symbols"].get(symbol, None)
    
    def get_summary(self):
        """Return brain summary"""
        g = self.data["global"]
        global_wr = g["total_wins"] / g["total_trades"] if g["total_trades"] > 0 else 0
        
        symbols = []
        for sym, data in self.data["symbols"].items():
            symbols.append({
                "symbol": sym,
                "trades": data["trades"],
                "win_rate": data["win_rate"],
                "total_pnl": data["total_pnl"],
                "best_mode": data.get("best_mode", "N/A"),
                "lot_mult": data["lot_multiplier"],
                "conf_adj": data["confidence_adjust"],
                "on_cooldown": data.get("cooldown_until") is not None
            })
        
        # Sort by total P/L descending
        symbols.sort(key=lambda x: x["total_pnl"], reverse=True)
        
        return {
            "global": {"trades": g["total_trades"], "wins": g["total_wins"], "losses": g["total_losses"], "win_rate": global_wr},
            "symbols": symbols
        }
