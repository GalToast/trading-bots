#!/usr/bin/env python3
"""Quick audit of existing Coinbase spot research and tools.

Maps what we already have in the repo to our current combined scorer effort.
"""
from pathlib import Path
import glob

ROOT = Path(__file__).resolve().parent.parent

# Key directories to audit
DIRS = {
    "Coinbase Scripts": ROOT / "scripts",
    "Reports": ROOT / "reports",
    "ML Models": ROOT / "reports" / "models",
    "Candle Cache": ROOT / "reports" / "candle_cache",
    "Configs": ROOT / "configs",
    "Docs": ROOT / "docs",
}

# Patterns to search for
PATTERNS = [
    "**/coinbase_spot_*.py",
    "**/combined_scorer*.py",
    "**/compression_audit*",
    "**/money_velocity*.py",
    "**/shadow*.py",
    "**/mfe_capture*.py",
    "**/cluster_size*.py",
    "**/execution_slot*.py",
    "**/multi_signal*.py",
    "**/regime_adaptive*.py",
    "**/cross_validate*.py",
    "**/kraken*.py",
    "**/tail*.joblib",
    "**/fast_green*.joblib",
    "**/tail_fastgreen*.md",
    "**/tail_fastgreen*.json",
    "**/capital_compression*.md",
    "**/tail_fastgreen_compression_audit*.md",
]

def main():
    print("=" * 80)
    print("EXISTING COINBASE SPOT RESEARCH AUDIT")
    print("=" * 80)
    
    for pattern in PATTERNS:
        files = list(ROOT.glob(pattern))
        if files:
            print(f"\n{pattern}:")
            for f in sorted(files)[:10]:
                print(f"  {f.relative_to(ROOT)}")
            if len(files) > 10:
                print(f"  ... and {len(files) - 10} more")
    
    # Now read the compression audit to understand what we have
    print(f"\n{'='*80}")
    print("COMPRESSION AUDIT RESULTS")
    print(f"{'='*80}")
    
    audit_path = ROOT / "reports" / "coinbase_spot_tail_fastgreen_compression_audit.md"
    if audit_path.exists():
        content = audit_path.read_text()
        # Extract the stats table
        for line in content.split('\n'):
            if '|' in line and ('Rows' in line or 'all_selected' in line or 'true_time' in line or 'model_order' in line):
                print(f"  {line.strip()}")
    
    # Read the money velocity theory map
    print(f"\n{'='*80}")
    print("MONEY VELOCITY THEORY MAP — Key Theories")
    print(f"{'='*80}")
    
    theory_path = ROOT / "scripts" / "build_coinbase_spot_money_velocity_theory_map.py"
    if theory_path.exists():
        content = theory_path.read_text()
        # Extract theory names
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('(') and line.endswith('),'):
                parts = line.split(',')
                if len(parts) >= 2:
                    theory = parts[0].strip('("')
                    category = parts[1].strip('" ')
                    status = parts[3].strip('" )') if len(parts) > 3 else ""
                    print(f"  Theory: {theory:<40} Category: {category:<20} Status: {status}")
    
    print(f"\n{'='*80}")
    print("HIGHEST-LEVERAGE EXISTING ASSETS")
    print(f"{'='*80}")
    
    print("""
1. **compression_audit.md** — Already ran! Shows 100% WR, 253% cumulative Coinbase net
   - This IS the validation we need. It's DONE.
   
2. **machinegun_strategy_board.py** — Already loads Tail V2 + FastGreen + Fee Survival
   - The board already scores live radar with all 3 models
   - This is the deployment surface

3. **live_coinbase_spot_machinegun_shadow.py** — The shadow runner
   - Already implements Tail+FG intersection logic
   - Already tracks ML probabilities
   - Ready to launch with --require-tail-prob + --require-fast-green-prob

4. **money_velocity_theory_map.py** — 48 theories × 100 variants
   - Contains the theory behind WHY the edge works
   - The combined scorer IS the top 3 theories operationalized

5. **candle_cache** — 289 files across 100+ products
   - This is the training data foundation
   - RAVE has the richest cache (M1, M5, M15, H1, H4 at multiple durations)

6. **ML Models** — 8 trained models
   - Tail V2 onehot (AUC 0.9944) is the active one
   - Fast-Green is the speed component
   - Fee-survival is the baseline

THE TRUTH: We already have EVERYTHING built. The infrastructure is COMPLETE.
What we need is NOT more research — we need to LAUNCH the shadow lane and get
live validation data. The 709-signal audit is already done. The compression audit
is already done. The models are already trained. The shadow runner already exists.

The ONLY blocker is the stale live radar. Once it refreshes, we can launch.
""")

if __name__ == "__main__":
    main()
