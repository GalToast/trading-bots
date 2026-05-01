#!/usr/bin/env python3
"""
M5 Warp Cross-Market Edge Ranking
==================================
Pulls all M5 Warp state files + event logs and produces a single ranked table:
- Spread efficiency (spread/step ratio)
- $/close quality
- Reset rate
- Net PnL

Usage:
    python scripts/rank_m5_warp_edges.py              # One-shot ranking
    python scripts/rank_m5_warp_edges.py --output reports/m5_warp_edge_ranking.md
"""
import json
import glob
import os
import math
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "reports"

# All known M5 Warp state file patterns
STATE_PATTERNS = [
    # BTC
    "*btcusd*m5*warp*state.json",
    "*btcusd*m15*warp*state.json",
    # ETH
    "*ethusd*m5*warp*state.json",
    "*ethusd*m15*warp*state.json",
    # SOL
    "*solusd*m5*warp*state.json",
    # XRP
    "*xrpusd*m5*warp*state.json",
    # FX
    "*gbpusd*m5*warp*state.json",
    "*usdjpy*m5*warp*state.json",
    "*audusd*m5*warp*state.json",
    "*eurusd*m5*warp*state.json",
    "*nzdusd*m5*warp*state.json",
    "*usdcad*m5*warp*state.json",
    # Indices/Gold
    "*xauusd*m5*warp*state.json",
    "*nas100*m5*warp*state.json",
    "*us30*m5*warp*state.json",
]

def load_state(filepath: str) -> dict:
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def estimate_spread(symbol: str) -> float:
    """Rough spread estimates in price units for spread/step ratio calculation."""
    spreads = {
        "BTCUSD": 1.0,
        "ETHUSD": 0.1,
        "SOLUSD": 0.01,
        "XRPUSD": 0.0001,
        "GBPUSD": 0.00001,
        "USDJPY": 0.001,
        "AUDUSD": 0.00001,
        "EURUSD": 0.00001,
        "NZDUSD": 0.00001,
        "USDCAD": 0.00001,
        "XAUUSD": 0.05,
        "NAS100": 0.25,
        "US30": 0.5,
    }
    sym_upper = symbol.upper()
    for k, v in spreads.items():
        if k in sym_upper:
            return v
    return 0.0

def extract_edge(state: dict, filepath: str) -> dict:
    """Extract edge metrics from a state file."""
    if not state:
        return None
    
    symbols = state.get("symbols", {})
    if not symbols:
        return None
    
    sym_name = list(symbols.keys())[0]
    sym_data = symbols[sym_name]
    
    closes = sym_data.get("realized_closes", 0)
    net = sym_data.get("realized_net_usd", 0.0)
    resets = sym_data.get("anchor_resets", 0)
    opens = len(sym_data.get("open_tickets", []))
    step = sym_data.get("base_step_px", 0)
    anchor = sym_data.get("anchor", 0)
    timeframe = sym_data.get("timeframe", "?")
    
    # Heartbeat
    heartbeat = state.get("runner", {}).get("heartbeat_at", "unknown")
    
    # Derived metrics
    per_close = net / closes if closes > 0 else 0
    reset_rate = resets / max(closes, 1)
    spread = estimate_spread(sym_name)
    spread_step_ratio = spread / step if step > 0 else 0
    
    # Score: $/close weighted by reliability (fewer resets = better)
    reliability = 1.0 / (1.0 + reset_rate)  # 0 resets = 1.0, 1 reset/close = 0.5
    score = per_close * reliability
    
    # Detect coefficient from filename
    fname = os.path.basename(filepath).lower()
    coeff = "1.5x"
    if "1x" in fname or "1.0" in fname:
        coeff = "1.0x"
    elif "0.8" in fname or "0.5" in fname:
        coeff = "0.8x"
    
    return {
        "symbol": sym_name,
        "timeframe": timeframe,
        "coefficient": coeff,
        "step": step,
        "closes": closes,
        "net": net,
        "per_close": per_close,
        "resets": resets,
        "reset_rate": reset_rate,
        "open": opens,
        "spread_step_ratio": spread_step_ratio,
        "score": score,
        "reliability": reliability,
        "heartbeat": heartbeat,
        "filepath": os.path.basename(filepath),
    }

def rank_edges(edges: list) -> list:
    """Rank edges by score (descending)."""
    # Sort by: positive $/close first, then by score, then by closes count
    edges.sort(key=lambda e: (
        1 if e["per_close"] > 0 else 0,  # Positive edges first
        e["score"],                        # Higher score = better
        e["closes"],                       # More closes = more confidence
    ), reverse=True)
    return edges

def main():
    edges = []
    
    for pattern in STATE_PATTERNS:
        matches = glob.glob(str(REPORTS / pattern))
        for filepath in matches:
            if not filepath.endswith("_state.json"):
                continue
            # Skip backup/old files
            if "backup" in filepath.lower() or ".old" in filepath.lower():
                continue
            
            state = load_state(filepath)
            if not state:
                continue
            
            edge = extract_edge(state, filepath)
            if edge:
                edges.append(edge)
    
    edges = rank_edges(edges)
    
    # Output
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append(f"# M5 Warp Cross-Market Edge Ranking — {now}\n")
    lines.append(f"| Rank | Symbol | TF | Coeff | Step | Closes | Net $ | $/close | Resets | Reset Rate | Spread/Step | Score | Status |")
    lines.append(f"|------|--------|----|-------|------|--------|-------|---------|--------|------------|-------------|-------|--------|")
    
    for i, edge in enumerate(edges, 1):
        if edge["closes"] > 0 and edge["net"] > 0:
            status = "✅"
        elif edge["closes"] > 0 and edge["net"] < 0:
            status = "❌"
        elif edge["open"] > 0 and edge["closes"] == 0:
            status = "🔄"
        elif edge["resets"] > 0:
            status = "⚠️"
        else:
            status = "⏳"
        
        lines.append(
            f"| {i} | {edge['symbol']} | {edge['timeframe']} | {edge['coefficient']} | "
            f"{edge['step']:.5f} | {edge['closes']} | ${edge['net']:+.2f} | "
            f"${edge['per_close']:.2f} | {edge['resets']} | {edge['reset_rate']:.2f} | "
            f"{edge['spread_step_ratio']:.2f} | {edge['score']:.2f} | {status} |"
        )
    
    # Summary by coefficient
    lines.append("\n## Coefficient Comparison\n")
    coeff_groups = {}
    for edge in edges:
        c = edge["coefficient"]
        if c not in coeff_groups:
            coeff_groups[c] = []
        coeff_groups[c].append(edge)
    
    lines.append(f"| Coeff | Lanes | Total Closes | Total Net $ | Avg $/close | Avg Score |")
    lines.append(f"|-------|-------|-------------|-------------|-------------|-----------|")
    
    for coeff in sorted(coeff_groups.keys()):
        group = coeff_groups[coeff]
        total_closes = sum(e["closes"] for e in group)
        total_net = sum(e["net"] for e in group)
        avg_per_close = total_net / total_closes if total_closes > 0 else 0
        avg_score = sum(e["score"] for e in group) / len(group)
        
        lines.append(f"| {coeff} | {len(group)} | {total_closes} | ${total_net:+.2f} | ${avg_per_close:.2f} | {avg_score:.2f} |")
    
    # Top 3 actionable edges
    positive = [e for e in edges if e["per_close"] > 0 and e["closes"] >= 3]
    if positive:
        lines.append("\n## Top 3 Validated Edges (3+ closes, positive $/close)\n")
        for i, edge in enumerate(positive[:3], 1):
            lines.append(f"{i}. **{edge['symbol']} {edge['timeframe']} {edge['coefficient']}**: ${edge['per_close']:.2f}/close, {edge['closes']} closes, {edge['reset_rate']:.2f} reset rate")
    else:
        lines.append("\n## No edges yet meet the 3+ close, positive $/close threshold\n")
        lines.append("**Action:** Let lanes trade. Need 3+ closes per lane for reliable ranking.")
    
    output = "\n".join(lines)
    
    output_path = REPORTS / "m5_warp_edge_ranking.md"
    output_path.write_text(output, encoding='utf-8')
    print(f"Saved to {output_path}")
    print()
    print(output)

if __name__ == "__main__":
    main()
