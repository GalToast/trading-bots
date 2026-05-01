#!/usr/bin/env python3
"""Mad Scientist Patch: Spread/MER Admission Gate for ALL modes.

This patch adds a universal spread + MER admission gate that applies to
BOTH systemic and idiosyncratic execution modes.

Current problem:
- Spread/MER checks ONLY apply in systemic mode (cluster_size >= 20)
- In idiosyncratic mode, NO spread filter — all products admitted
- Defaults are too tight: spread>=100, MER>=3.5 — misses profitable products

Proposed fix:
- Apply spread>=50 and MER>=2.5 to ALL products regardless of mode
- Based on data: 83% WR at spread>=50, 100% WR when combined with MER>=2.5
"""

# ============================================================================
# PATCH 1: __init__ parameters (line ~100-103)
# ============================================================================
# CHANGE FROM:
#         systemic_min_entry_spread_bps: float = 100.0,
#         systemic_min_entry_mer: float = 3.5,
#
# CHANGE TO:
#         min_entry_spread_bps: float = 50.0,
#         min_entry_mer: float = 2.5,
#         systemic_min_entry_spread_bps: float | None = None,  # deprecated
#         systemic_min_entry_mer: float | None = None,         # deprecated

# ============================================================================
# PATCH 2: Instance variables (line ~125-126)
# ============================================================================
# CHANGE FROM:
#         self.systemic_min_entry_spread_bps = max(0.0, float(systemic_min_entry_spread_bps))
#         self.systemic_min_entry_mer = max(0.0, float(systemic_min_entry_mer))
#
# CHANGE TO:
#         self.min_entry_spread_bps = max(0.0, float(min_entry_spread_bps))
#         self.min_entry_mer = max(0.0, float(min_entry_mer))
#         # Backwards compatibility
#         if systemic_min_entry_spread_bps is not None:
#             self.min_entry_spread_bps = max(0.0, float(systemic_min_entry_spread_bps))
#         if systemic_min_entry_mer is not None:
#             self.min_entry_mer = max(0.0, float(systemic_min_entry_mer))

# ============================================================================
# PATCH 3: eligible_rows() method (line ~380-388)
# ============================================================================
# FIND THIS BLOCK (inside the for loop, after candidate streak check):
#
#             if self.candidate_streaks.get(pid, 0) < self.entry_confirmation_polls:
#                 continue
#             # NEW: Pulse Score Veto (protects against idiosyncratic dumping)
#             pulse = to_float(row.get("pulse_score"), default=0.0)
#             if pulse < 0.0:
#                 print(f"  VETO (NEGATIVE PULSE): {pid} (score: {pulse:.2f})")
#                 continue
#
#             if mode == "systemic":
#                 mer = to_float(row.get("mer"), default=0.0)
#                 spread_bps = to_float(row.get("spread_bps"), default=0.0)
#                 if mer < self.systemic_min_entry_mer:
#                     print(f"  VETO (SYSTEMIC LOW MER): {pid} (mer: {mer:.2f})")
#                     continue
#                 if spread_bps < self.systemic_min_entry_spread_bps:
#                     print(f"  VETO (SYSTEMIC LOW SPREAD): {pid} (spread_bps: {spread_bps:.2f})")
#                     continue
#
# REPLACE WITH:
#
#             if self.candidate_streaks.get(pid, 0) < self.entry_confirmation_polls:
#                 continue
#             # NEW: Pulse Score Veto (protects against idiosyncratic dumping)
#             pulse = to_float(row.get("pulse_score"), default=0.0)
#             if pulse < 0.0:
#                 print(f"  VETO (NEGATIVE PULSE): {pid} (score: {pulse:.2f})")
#                 continue
#
#             # UNIVERSAL SPREAD/MER GATE (applies to ALL modes)
#             # Data-backed thresholds: spread>=50bps + MER>=2.5 = 100% WR (4/4 wins)
#             mer = to_float(row.get("mer"), default=0.0)
#             spread_bps = to_float(row.get("spread_bps"), default=0.0)
#             if mer < self.min_entry_mer:
#                 print(f"  VETO (LOW MER): {pid} (mer: {mer:.2f} < {self.min_entry_mer:.2f})")
#                 continue
#             if spread_bps < self.min_entry_spread_bps:
#                 print(f"  VETO (LOW SPREAD): {pid} (spread: {spread_bps:.1f}bps < {self.min_entry_spread_bps:.1f}bps)")
#                 continue
#
#             # Keep systemic-specific checks if still needed
#             if mode == "systemic":
#                 # Additional systemic-only vetos can go here
#                 pass

# ============================================================================
# PATCH 4: CLI args (line ~800-805)
# ============================================================================
# CHANGE FROM:
#     parser.add_argument("--systemic-min-entry-spread-bps", type=float, default=100.0)
#     parser.add_argument("--systemic-min-entry-mer", type=float, default=3.5)
#
# CHANGE TO:
#     parser.add_argument("--min-entry-spread-bps", type=float, default=50.0)
#     parser.add_argument("--min-entry-mer", type=float, default=2.5)
#     # Deprecated but kept for backwards compatibility
#     parser.add_argument("--systemic-min-entry-spread-bps", type=float, default=None)
#     parser.add_argument("--systemic-min-entry-mer", type=float, default=None)

# ============================================================================
# PATCH 5: Engine instantiation (line ~864-865)
# ============================================================================
# CHANGE FROM:
#             systemic_min_entry_spread_bps=args.systemic_min_entry_spread_bps,
#             systemic_min_entry_mer=args.systemic_min_entry_mer,
#
# CHANGE TO:
#             min_entry_spread_bps=args.min_entry_spread_bps or args.systemic_min_entry_spread_bps or 50.0,
#             min_entry_mer=args.min_entry_mer or args.systemic_min_entry_mer or 2.5,

# ============================================================================
# PATCH 6: snapshot() method (line ~217-218)
# ============================================================================
# CHANGE FROM:
#             "systemic_min_entry_spread_bps": round(self.systemic_min_entry_spread_bps, 6),
#             "systemic_min_entry_mer": round(self.systemic_min_entry_mer, 6),
#
# CHANGE TO:
#             "min_entry_spread_bps": round(self.min_entry_spread_bps, 6),
#             "min_entry_mer": round(self.min_entry_mer, 6),

print("This is a patch specification — not an executable script.")
print("Apply the changes manually to live_kraken_spot_frontier_maker_machinegun_shadow.py")
print("")
print("Summary of changes:")
print("  1. __init__: Add min_entry_spread_bps=50.0, min_entry_mer=2.5")
print("  2. eligible_rows: Move spread/MER gate outside systemic block")
print("  3. CLI args: Update to new parameter names")
print("  4. snapshot(): Update field names")
