import json

with open("reports/_multi_strategy_scan_results.json", "r") as f:
    results = json.load(f)

winners = [r for r in results if "error" not in r and r.get("realized_pnl", 0) > 0]
winners.sort(key=lambda x: x["realized_pnl"], reverse=True)

print("=== POSITIVE EDGE RESULTS ===")
print(f"{'Strategy':>20}  {'Coin':>15}  Trades  WR%    PnL")
print('-' * 70)
for r in winners:
    print(f"{r['strategy']:>20}  {r['coin']:>15}  {r['closes']:>5}  {r['win_rate']:>4.0f}%  ${r['realized_pnl']:>8.2f}")

print("\n=== STRATEGY FAMILY SUMMARY ===")
strats = {}
for r in results:
    if "error" in r: continue
    s = r["strategy"]
    if s not in strats: strats[s] = {"pos": 0, "neg": 0, "total": 0.0}
    if r["realized_pnl"] > 0: strats[s]["pos"] += 1
    elif r["realized_pnl"] < 0: strats[s]["neg"] += 1
    strats[s]["total"] += r["realized_pnl"]

for s, d in sorted(strats.items(), key=lambda x: x[1]["total"], reverse=True):
    flag = "+" if d["total"] > 0 else "-"
    print(f"{flag} {s:>20}  coins+={d['pos']}  coins-={d['neg']}  total=${d['total']:+.2f}")
