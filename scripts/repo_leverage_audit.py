#!/usr/bin/env python3
"""Audit existing repo research and map to our combined scorer effort.

This inventories everything we've built and finds the highest-leverage
connections we haven't fully exploited yet.
"""
from pathlib import Path
import glob

ROOT = Path(__file__).resolve().parent.parent

# Categories of existing research
CATEGORIES = {
    "ML Models": "reports/models/coinbase_spot_*.joblib",
    "Training Tables": "reports/coinbase_spot_*_training_table*.csv",
    "Candle Cache": "reports/candle_cache/*.json",
    "Compression Audit": "reports/coinbase_spot_tail_fastgreen_compression_audit.*",
    "Shadow Runners": "scripts/live_coinbase_spot_*_shadow.py",
    "Strategy Boards": "scripts/build_coinbase_spot_*_board.py",
    "Radar/Pulse": "scripts/build_coinbase_spot_*_radar.py",
    "Fee Analysis": "scripts/coinbase_fee*.py",
    "GPU Foundry": "scripts/run_coinbase_spot_gpu_foundry.py",
    "Money Velocity": "scripts/build_coinbase_spot_money_velocity_theory_map.py",
    "Cross-Exchange": "scripts/cross_validate_kraken_with_coinbase.py",
    "Cluster Analysis": "scripts/cluster_size_analysis.py",
    "MFE Tracking": "scripts/mfe_capture_tracker.py",
    "Execution Slot": "scripts/execution_slot_compression.py",
    "Multi-Signal": "scripts/multi_signal_cycle_optimizer.py",
    "Temporal Features": "scripts/build_temporal_feature_lookup.py",
    "Repo Audit": "scripts/repo_research_audit.py",
}

def main():
    print("=" * 80)
    print("EXISTING COINBASE SPOT RESEARCH AUDIT")
    print("=" * 80)

    for category, pattern in CATEGORIES.items():
        files = glob.glob(str(ROOT / pattern))
        if files:
            print(f"\n{category}: {len(files)} files")
            for f in sorted(files)[:15]:
                print(f"  {Path(f).name}")
            if len(files) > 15:
                print(f"  ... and {len(files) - 15} more")

    # Now read the compression audit to understand what we have
    print(f"\n{'='*80}")
    print("COMPRESSION AUDIT RESULTS (V2 Tail + FastGreen intersection)")
    print(f"{'='*80}")
    
    audit_path = ROOT / "reports" / "coinbase_spot_tail_fastgreen_compression_audit.md"
    if audit_path.exists():
        content = audit_path.read_text()
        # Extract the stats table
        for line in content.split('\n'):
            if '|' in line and ('Rows' in line or 'all_selected' in line or 'true_time' in line or 'model_order' in line):
                print(f"  {line.strip()}")
    else:
        print("  Not found — run: python scripts/build_coinbase_spot_tail_fastgreen_compression_audit.py")

    # Now identify the HIGHEST-LEVERAGE connections
    print(f"\n{'='*80}")
    print("HIGHEST-LEVERAGE CONNECTIONS WE HAVEN'T FULLY EXPLOITED")
    print(f"{'='*80}")

    connections = [
        {
            "name": "Money Velocity Theory Map → Combined Scorer",
            "existing": "48 theories × 100 variants = 4,800 hypotheses",
            "leverage": "The combined scorer IS the top 3 theories (live_fee_cleared_breakout, bubble_ignition_reclaim, dump_exhaustion_rebound). But we could score ALL 48 theories with the combined models and find hidden edges.",
            "priority": "HIGH",
        },
        {
            "name": "GPU Foundry → Combined Scorer",
            "existing": "720 geometries × 21 products = 15,120 combinations tested",
            "leverage": "The foundry found that dump_reclaim + compression_pop were positive. But it used RAW geometry, not ML-filtered. Re-run the foundry through the combined scorer — only test geometries that pass Tail+FG intersection.",
            "priority": "HIGH",
        },
        {
            "name": "Product Filters → Combined Scorer",
            "existing": "72h microstructure analysis: spread, range, persistence, compression",
            "leverage": "The product filters show WHICH products have capturable moves. Filter the combined scorer to only products with >1% range frequency AND positive persistence. This eliminates products where MFE can't be captured.",
            "priority": "HIGH",
        },
        {
            "name": "Dissonance Board → Combined Scorer",
            "existing": "Cross-timeframe conflict detection",
            "leverage": "Add dissonance as a veto signal. If Tail+FG agree but dissonance board says timeframes conflict, BLOCK the entry. This should reduce false positives during choppy regimes.",
            "priority": "MEDIUM",
        },
        {
            "name": "Bear Velocity Board → Combined Scorer",
            "existing": "Detects products in dump mode",
            "leverage": "If bear velocity is active, the combined scorer should REQUIRE higher thresholds (Tail≥0.98 instead of 0.95). This is a regime-adaptive threshold that's simpler than full clustering.",
            "priority": "MEDIUM",
        },
        {
            "name": "Shadow Trade Forensics → MFE Tracking",
            "existing": "Post-trade analysis of shadow lane entries/exits",
            "leverage": "Combine forensics with MFE tracker. For every shadow trade, compute: predicted MFE, actual MFE, captured MFE, fee impact. This gives us the REAL capture rate distribution, not just model predictions.",
            "priority": "HIGH",
        },
        {
            "name": "Hot Capital Router → Dual-Mode Execution",
            "existing": "Routes capital to hottest products",
            "leverage": "The router already ranks products by live heat. Use it to decide idiosyncratic vs systemic mode: if router shows 1-3 hot products → idiosyncratic mode (80% deploy). If 10+ hot products → systemic mode (20% deploy).",
            "priority": "HIGH",
        },
        {
            "name": "Candle Cache (30d) → Retraining",
            "existing": "289 candle files, RAVE has 60d M5/M15/H1/H4",
            "leverage": "The ML models were trained on 7d data. The 30d cache gives us 4x more training data. Retrain with 30d candles → more robust features, better generalization, higher AUC.",
            "priority": "MEDIUM",
        },
        {
            "name": "Piranha Shadow Lanes → Single-Product Validation",
            "existing": "Per-symbol shadow runners for XRP, DOGE, SUI, ADA",
            "leverage": "The piranha lanes have been running for weeks. They have REAL entry/exit data with actual MFE capture rates. Use this as the ground truth to calibrate the combined scorer's MFE predictions.",
            "priority": "HIGH",
        },
        {
            "name": "Fee Tier Resolution → Realistic PnL",
            "existing": "coinbase_fee_model.py calls API for actual taker/maker rates",
            "leverage": "The combined scorer uses hardcoded 240bps. The fee model knows the REAL rate. If we're on Intro 1 (120bps/side), use that. If we qualify for VIP, use VIP rates. This changes the break-even capture rate dramatically.",
            "priority": "MEDIUM",
        },
    ]

    for i, conn in enumerate(connections, 1):
        print(f"\n{i}. **{conn['name']}** — Priority: {conn['priority']}")
        print(f"   Existing: {conn['existing']}")
        print(f"   Leverage: {conn['leverage']}")

    print(f"\n{'='*80}")
    print("RECOMMENDED NEXT ACTIONS")
    print(f"{'='*80}")
    print("""
1. IMMEDIATE: Add dissonance board veto to combined scorer (blocks choppy regime entries)
2. IMMEDIATE: Wire bear velocity board into regime-adaptive thresholds
3. IMMEDIATE: Run shadow trade forensics through MFE tracker for real capture rates
4. SHORT-TERM: Re-run GPU foundry through combined scorer filter
5. SHORT-TERM: Use piranha lane data as ground truth for MFE calibration
6. MEDIUM-TERM: Retrain with 30d candle cache for more robust models
7. MEDIUM-TERM: Integrate hot capital router for idiosyncratic/systemic mode detection
""")

if __name__ == "__main__":
    main()
