#!/usr/bin/env python3
"""Verify the 0.8x range formula against champion data."""

cases = [
    ('BTC M5', 64.50, 1.34, 100),
    ('BTC M15', 123.86, 1.73, 75),
    ('ETH M15', 5.55, 1.67, 5),
    ('SOL M5', 0.124, 1.17, 0.12),
]

print("=" * 70)
print("VERIFYING: step = 0.8 x typical_bar_range")
print("=" * 70)

for name, atr, ratio, step in cases:
    rng = atr * ratio
    coeff = step / rng
    formula_08 = 0.8 * rng
    diff_pct = (step - formula_08) / step * 100
    print(f"{name}: Range=${rng:.3f}, actual={coeff:.2f}x Range, 0.8x=${formula_08:.3f}, diff={diff_pct:+.0f}%")

print()
print("BACKSOLVED COEFFICIENTS (what coefficient actually fits?):")
for name, atr, ratio, step in cases:
    rng = atr * ratio
    coeff = step / rng
    print(f"  {name}: step = {coeff:.2f} x Range")

print()
print("VERDICT: Is 0.8x universal?")
coeffs = [(atr*ratio, step, step/(atr*ratio)) for _, atr, ratio, step in cases]
avg_coeff = sum(c[2] for c in coeffs) / len(coeffs)
print(f"  Average coefficient: {avg_coeff:.2f}")
print(f"  Range: {min(c[2] for c in coeffs):.2f}x to {max(c[2] for c in coeffs):.2f}x")
print(f"  0.8x is correct? {'YES' if all(abs(c[2]-0.8)<0.15 for c in coeffs) else 'NO - actual avg is {:.2f}x'.format(avg_coeff)}")
