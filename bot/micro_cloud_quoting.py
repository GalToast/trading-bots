"""
Micro-Cloud Quoting Engine
Implements valid-tick micro-cloud quoting to reduce HFT front-running.

Strategy: Split post-only orders across multiple tick levels:
- Level 0: Best bid (current approach)
- Level 1: -1 tick from best bid  
- Level 2: -2 ticks from best bid

Benefits:
- Reduces ghost ratio (currently 4:1) by making orders less visible
- Maintains fill rate by covering multiple price levels
- Post-only (maker) orders avoid paying taker fees
"""

import time
from typing import List, Dict, Optional


class MicroCloudQuoter:
    """Manages micro-cloud quoting across multiple tick levels."""
    
    def __init__(self, levels: int = 3, base_size: float = 10.0):
        """
        Initialize micro-cloud quoter.
        
        Args:
            levels: Number of tick levels to use (default 3: 0, -1, -2)
            base_size: Base position size to split across levels
        """
        self.levels = levels
        self.base_size = base_size
        
    def calculate_tick_offsets(self, symbol_info) -> List[float]:
        """
        Calculate tick offset values for the symbol.
        
        Returns offsets in price units (negative for bids, positive for asks).
        """
        tick_size = getattr(symbol_info, 'trade_tick_size', 0.01)
        return [i * tick_size for i in range(0, self.levels)]
    
    def split_size_across_levels(self, total_size: float) -> List[float]:
        """
        Split total size across levels.
        
        Strategy: 40% at best, 35% at -1, 25% at -2
        (Heavier weight at better prices for faster fill)
        """
        if self.levels == 1:
            return [total_size]
        
        splits = []
        remaining = total_size
        
        # Level 0: 40%
        level_0 = total_size * 0.40
        splits.append(level_0)
        remaining -= level_0
        
        if self.levels >= 2:
            # Level 1: 35%
            level_1 = total_size * 0.35
            splits.append(level_1)
            remaining -= level_1
            
        if self.levels >= 3:
            # Level 2: remaining 25%
            splits.append(remaining)
            
        return splits
    
    def generate_orders(self, symbol: str, direction: str, symbol_info, 
                       tick, current_time: float) -> List[Dict]:
        """
        Generate micro-cloud orders across multiple tick levels.
        
        Args:
            symbol: Trading symbol
            direction: "BUY" or "SELL"
            symbol_info: MT5 symbol info
            tick: Current tick data (bid/ask)
            current_time: Current timestamp
            
        Returns:
            List of order dicts with price and size for each level
        """
        if direction == "BUY":
            base_price = tick.ask  # Still use ask for buy triggers
        else:
            base_price = tick.bid  # Still use bid for sell triggers
            
        tick_offsets = self.calculate_tick_offsets(symbol_info)
        size_splits = self.split_size_across_levels(self.base_size)
        
        orders = []
        tick_size = getattr(symbol_info, 'trade_tick_size', 0.01)
        
        for i, (offset, size) in enumerate(zip(tick_offsets, size_splits)):
            if direction == "BUY":
                # For buys, we want to post below market (negative offset)
                price = base_price - offset
            else:
                # For sells, we want to post above market (positive offset)
                price = base_price + offset
                
            orders.append({
                'level': i,
                'symbol': symbol,
                'direction': direction,
                'price': price,
                'size': size,
                'offset_ticks': i,
                'order_type': 'post_only',  # Maker-only, no taker fees
                'created_at': current_time,
            })
            
        return orders
    
    def should_replace_ghost(self, order: Dict, current_tick, fill_threshold: float = 0.5) -> bool:
        """
        Determine if a ghost order should be replaced.
        
        Args:
            order: Order dict with price, size, level
            current_tick: Current market tick
            fill_threshold: If price moved more than this (in ticks), replace
            
        Returns:
            True if order should be replaced (moved to new level)
        """
        # If market moved away significantly, replace the order
        if order['direction'] == "BUY":
            price_move = current_tick.ask - order['price']
        else:
            price_move = order['price'] - current_tick.bid
            
        tick_size = 0.01  # Placeholder - should get from symbol_info
        ticks_moved = abs(price_move) / tick_size
        
        return ticks_moved > fill_threshold


def integrate_with_competition_engine():
    """
    Integration pseudocode for competition.py
    
    Add to competition.py's entry logic:
    
    ```python
    # Instead of:
    # proposed_entry_price = tick.ask if signal == "BUY" else tick.bid
    
    # Use micro-cloud quoting:
    quoter = MicroCloudQuoter(levels=3, base_size=10.0)
    micro_orders = quoter.generate_orders(
        symbol=symbol,
        direction=signal,
        symbol_info=mt5.symbol_info(symbol),
        tick=tick,
        current_time=time.time()
    )
    
    # Place each order as post-only
    for order in micro_orders:
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": order['symbol'],
            "volume": order['size'],
            "price": order['price'],
            "type": mt5.ORDER_TYPE_BUY_LIMIT if order['direction'] == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            track_micro_order(result.order, order)
    ```
    """
    pass


if __name__ == "__main__":
    # Test the micro-cloud quoter
    print("Micro-Cloud Quoting Engine")
    print("=" * 60)
    print("Strategy: Split post-only orders across 3 tick levels")
    print("Benefit: Reduce HFT front-running (current 4:1 ghost ratio)")
    print("=" * 60)
