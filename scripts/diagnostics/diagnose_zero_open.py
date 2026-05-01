"""Diagnose why XRP M5 and ETH M5 lanes have 0 open positions."""
import MetaTrader5 as mt5
import json

mt5.initialize()

# Check XRP M5
print("=== XRP M5 Diagnosis ===")
xrp_info = mt5.symbol_info("XRPUSD")
if xrp_info:
    print(f"  Bid: {xrp_info.bid}")
    print(f"  Ask: {xrp_info.ask}")
    print(f"  Point: {xrp_info.point}")
    print(f"  Digits: {xrp_info.digits}")
    # The step is $0.0016
    # At current price ~1.37, the step in points would be:
    step_dollars = 0.0016
    step_points = step_dollars / xrp_info.point
    print(f"  Step $0.0016 = {step_points:.0f} points")
    print(f"  Grid levels would be at: ${xrp_info.bid - 0.0016:.4f}, ${xrp_info.bid - 2*0.0016:.4f}, etc.")
else:
    print("  XRPUSD not available")

# Check ETH M5
print("\n=== ETH M5 Diagnosis ===")
eth_info = mt5.symbol_info("ETHUSD")
if eth_info:
    print(f"  Bid: {eth_info.bid}")
    print(f"  Ask: {eth_info.ask}")
    print(f"  Point: {eth_info.point}")
    print(f"  Digits: {eth_info.digits}")
    # ETH M5 $3 step
    for step in [3.0, 5.0]:
        step_points = step / eth_info.point
        print(f"  Step ${step} = {step_points:.0f} points")
        print(f"    Grid levels: ${eth_info.bid - step:.2f}, ${eth_info.bid - 2*step:.2f}, etc.")
else:
    print("  ETHUSD not available")

# Check SOL M5 (which IS opening positions)
print("\n=== SOL M5 Diagnosis (for comparison) ===")
sol_info = mt5.symbol_info("SOLUSD")
if sol_info:
    print(f"  Bid: {sol_info.bid}")
    print(f"  Ask: {sol_info.ask}")
    print(f"  Point: {sol_info.point}")
    step = 0.12
    step_points = step / sol_info.point
    print(f"  Step ${step} = {step_points:.0f} points")
    print(f"    Grid levels: ${sol_info.bid - step:.2f}, ${sol_info.bid - 2*step:.2f}")
else:
    print("  SOLUSD not available")

mt5.shutdown()
