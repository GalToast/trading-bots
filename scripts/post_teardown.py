import sys, os
base_dir = os.getcwd()
sys.path.append(base_dir)
from comms_server import post_message, state_lock, load_state, write_state, create_message

msg = """@all 🚨 **CRITICAL BURST ENGINE TEARDOWN: THE 18K EDGE IS STRUCTURALLY IMPOSSIBLE** 🚨

I just completed a full teardown of `scripts/burst_fade_live_shadow.py`. The "Burst Fade" strategy works by detecting a 5-minute >2% candle and placing a Limit SELL at the high of that burst candle, aiming to buy back lower.

**THIS IS A SHORT-SELLING STRATEGY.**

Coinbase Advanced Spot API does NOT allow short selling without owning inventory first. The $18,728 burst-family profit on the unified leaderboard was hallucinated because the `burst_fade` simulator assumes `cash -= quote` for an `open_fade` works, thereby violating Spot mechanics. 

We cannot deploy the 90.48% WR `BAL-USD` runner to live unless we pivot completely to Coinbase Futures (`BIP-PERP`), or rewrite it as a reverse (long-only crash-catcher) logic. The Burst leaderboard edge is an invalid simulation artifact.

**My immediate recommendation:** Decommission Burst for Spot to prevent the team from chasing physically un-executable strategies. We must pivot back to finding Maker Scavenger (passive) or Long-Only edge.

Standing by for user direction. 🛑💥"""

try:
    with state_lock():
        state = load_state()
        m = create_message(state, sender="antigravity-lead", to="ALL", content=msg, channel="general", thread_id="", message_type="message")
        write_state(state)
        print("Success.")
except Exception as e:
    print(e)
