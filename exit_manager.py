"""
MT5 EXIT MANAGER - Companion to V10
Only manages exits, no new entries.
Sets broker-side stops so positions survive crashes.
"""
import MetaTrader5 as mt5
import time
from datetime import datetime
from brain import TradingBrain
from mt5_config import BOT_COMMENT_PREFIX, BOT_MAGIC, LOGIN, PASSWORD, SERVER

# === EXIT CONFIG ===
# PHILOSOPHY: Never realize a loss. Only bank profits or set crash protection.
# Losers recover or get offset by winners. Balance only grows.
STOP_LOSS = None         # Disabled — v10 bot manages exits
TAKE_PROFIT_USD = 2.00   # Minimum floor: close at +$2.00 (bank profit)
TAKE_PROFIT_ATR_SCALE = True  # Scale TP by symbol ATR for high-volatility symbols
MAX_HOLD_SEC = 3600      # 60 minutes max — give trades time to recover

def get_atr_scaled_take_profit(symbol):
    """Return ATR-scaled take profit for a symbol.

    For high-ATR symbols (NAS100, US30, BTC), the flat $2.00 TP is noise.
    We scale by typical ATR ranges per symbol type:
    - FX majors (EURUSD, GBPUSD): ~$0.0001-0.001 ATR → $2.00 floor
    - FX crosses (AUDCHF, GBPAUD): ~$0.001-0.005 ATR → $2.00 floor
    - Indices (NAS100, US30): ~$10-50 ATR → $10-20 TP
    - Crypto (BTCUSD, ETHUSD): ~$100-1000 ATR → $10-30 TP

    For symbols without known ATR data, return the $2.00 floor.
    """
    if not TAKE_PROFIT_ATR_SCALE:
        return TAKE_PROFIT_USD

    # Known symbol → typical daily ATR in dollar terms for 0.01 lot
    # These are approximate M15 ATR ranges observed in the bot
    SYMBOL_ATR_SCALE = {
        # Indices (high ATR, need wider TPs)
        "NAS100": 15.0,   # ~$15 M15 ATR → $15 TP
        "US30": 12.0,     # ~$12 M15 ATR → $12 TP
        "JPN225": 10.0,
        # Crypto (high ATR)
        "BTCUSD": 20.0,
        "ETHUSD": 8.0,
        "XRPUSD": 3.0,
        "SOLUSD": 5.0,
        # FX (low ATR, floor applies)
        "EURUSD": 0.0005,
        "GBPUSD": 0.0008,
        "USDJPY": 0.01,
        "NZDUSD": 0.0004,
        "AUDCHF": 0.0006,
        "GBPAUD": 0.001,
        "NZDCAD": 0.0008,
        "USDCHF": 0.0005,
    }

    sym = symbol.upper().replace("-", "")
    atr_dollars = SYMBOL_ATR_SCALE.get(sym, 0.0)

    if atr_dollars < 0.01:
        # FX symbol: ATR is in price units, not dollars. Floor applies.
        return TAKE_PROFIT_USD

    # Index/crypto: ATR is already in dollar terms
    # Scale: target ~1x ATR as TP (let winners run, but don't hold forever)
    return max(TAKE_PROFIT_USD, round(atr_dollars, 2))

# === GLOBALS ===
tracked_positions = {}  # ticket -> {entry_time, has_sl_tp}
brain = TradingBrain()

def is_bot_position(pos):
    comment = getattr(pos, "comment", "") or ""
    return getattr(pos, "magic", None) == BOT_MAGIC or comment.startswith(f"{BOT_COMMENT_PREFIX}-")

def get_position_mode(pos):
    comment = (getattr(pos, "comment", "") or "").upper()
    if "SNIPER" in comment:
        return "SNIPER"
    if "SHOTGUN" in comment:
        return "SHOTGUN"
    if "MACHINE_GUN" in comment:
        return "MACHINE_GUN"
    return "UNKNOWN"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def connect_mt5():
    """Connect to MT5 with retries"""
    for attempt in range(5):
        try:
            try:
                mt5.shutdown()
            except Exception:
                pass
            if mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
                info = mt5.account_info()
                if info is not None and int(info.login) == LOGIN:
                    log(f"Connected to MT5 account {int(info.login)} (attempt {attempt + 1})")
                    return True
                log(f"Wrong MT5 account after init (attempt {attempt + 1}): got={getattr(info, 'login', None)} expected={LOGIN}")
            time.sleep(5)
        except Exception as e:
            log(f"Connection error (attempt {attempt + 1}): {e}")
            time.sleep(5)
    return False

def set_broker_stops(ticket, direction, entry_price):
    """Set actual MT5 stop loss and take profit orders"""
    if ticket in tracked_positions and tracked_positions[ticket].get('has_sl_tp'):
        return True  # Already set
    
    tick = mt5.symbol_info_tick(mt5.positions_get(ticket=ticket)[0].symbol)
    if not tick:
        return False
    
    # Calculate SL/TP prices
    if direction == 0:  # BUY
        sl_price = round(entry_price - 0.0050, 5)  # ~50 pips
        tp_price = round(entry_price + 0.0080, 5)  # ~80 pips
    else:  # SELL
        sl_price = round(entry_price + 0.0050, 5)
        tp_price = round(entry_price - 0.0080, 5)
    
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl_price,
        "tp": tp_price,
    }
    
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        tracked_positions[ticket] = {'has_sl_tp': True}
        return True
    return False

def manage_exits():
    """Check all positions and close those meeting exit criteria"""
    positions = mt5.positions_get()
    if not positions:
        return 0
    
    closed_count = 0
    
    for pos in positions:
        if not is_bot_position(pos):
            continue

        ticket = pos.ticket
        symbol = pos.symbol
        pnl = pos.profit
        direction = pos.type  # 0=BUY, 1=SELL
        entry_time = pos.time
        entry_price = pos.price_open
        
        # Track new positions
        if ticket not in tracked_positions:
            tracked_positions[ticket] = {
                'entry_time': entry_time,
                'has_sl_tp': False
            }
            # Try to set broker stops
            set_broker_stops(ticket, direction, entry_price)
        
        hold_sec = time.time() - entry_time
        
        exit_triggered = False
        exit_reason = ""
        
        # 1. Stop loss — DISABLED: never realize a loss
        # if STOP_LOSS is not None and pnl <= STOP_LOSS:
        #     exit_triggered = True
        #     exit_reason = f"STOP (pnl=${pnl:.2f})"
        
        # 2. Take profit — bank gains (ATR-scaled for high-vol symbols)
        symbol_tp = get_atr_scaled_take_profit(symbol)
        if pnl >= symbol_tp:
            exit_triggered = True
            exit_reason = f"TARGET (pnl=${pnl:.2f})"

        # 3. Time exit — ONLY if profitable, never realize a loss
        elif hold_sec >= MAX_HOLD_SEC and pnl > 0:
            exit_triggered = True
            exit_reason = f"TIME_BANK (hold={int(hold_sec)}s, pnl=${pnl:.2f})"
        
        if exit_triggered:
            # Close position
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                continue
            
            price = tick.bid if direction == 0 else tick.ask
            order_type = mt5.ORDER_TYPE_SELL if direction == 0 else mt5.ORDER_TYPE_BUY
            
            # Try all filling modes
            for filling_mode in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": pos.volume,
                    "type": order_type,
                    "position": ticket,
                    "price": price,
                    "deviation": 50,
                    "magic": BOT_MAGIC,
                    "comment": f"{BOT_COMMENT_PREFIX} ExitMgr",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling_mode,
                }
                
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    closed_count += 1
                    mode = get_position_mode(pos)
                    brain.record_exit(symbol, pnl, mode, hold_sec)
                    log(f"  [{exit_reason}] Closed {symbol} #{ticket} | P/L: ${pnl:+.2f} | Hold: {int(hold_sec)}s")
                    tracked_positions.pop(ticket, None)
                    break
            else:
                log(f"  [EXIT FAILED] {symbol} #{ticket}: {result.comment if result else 'unknown'}")
    
    return closed_count

def run():
    log("=" * 60)
    log("MT5 EXIT MANAGER - Companion to V9")
    log("=" * 60)
    
    if not connect_mt5():
        log("Failed to connect to MT5. Exiting.")
        return
    
    log(f"Exit rules: Stop=${STOP_LOSS} | Target=${TAKE_PROFIT_USD} floor (ATR-scaled for indices/crypto) | Max Hold={MAX_HOLD_SEC}s")
    log("Monitoring all positions for exits...")
    log("=" * 60)
    
    total_closed = 0
    
    try:
        while True:
            # Check connection
            if not mt5.symbol_info_tick("EURUSD"):
                log("Connection lost. Reconnecting...")
                if not connect_mt5():
                    log("Reconnection failed. Waiting...")
                    time.sleep(30)
                    continue
            
            # Manage exits
            try:
                closed = manage_exits()
                total_closed += closed
                
                if closed > 0:
                    acct = mt5.account_info()
                    log(f"  Closed {closed} positions | Total: {total_closed} | Balance: ${acct.balance:.2f} | Equity: ${acct.equity:.2f}")
                
                # Status every 60 seconds
                positions = mt5.positions_get()
                pos_count = len(positions) if positions else 0
                if pos_count > 0:
                    pnl_sum = sum(p.profit for p in positions)
                    log(f"  Monitoring {pos_count} positions | Open P/L: ${pnl_sum:+.2f} | Exits: {total_closed}")
                
            except Exception as e:
                log(f"  Error in exit management: {e}")
            
            time.sleep(10)
    
    except KeyboardInterrupt:
        log("\nExit Manager stopped.")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    run()
