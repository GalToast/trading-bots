import json
f = r'reports/kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_events.jsonl'
c = mae3 = mae5 = mae10 = 0
worst = 0
with open(f, 'r') as fh:
    for line in fh:
        try:
            e = json.loads(line.strip())
        except:
            continue
        if e.get('action') == 'close_maker_shadow':
            c += 1
            m = e.get('min_net_pct_on_cost', 0)
            if m < -3: mae3 += 1
            if m < -5: mae5 += 1
            if m < -10: mae10 += 1
            if m < worst: worst = m
print(f'Closes: {c}')
print(f'MAE<-3%: {mae3}')
print(f'MAE<-5%: {mae5}')
print(f'MAE<-10%: {mae10}')
print(f'Worst MAE: {worst}')
adj = c - mae3 - 1
print(f'Shadow WR: {c-1}/{c} = {round((c-1)/c*100,1)}%')
print(f'Adj WR (3% cap): {adj}/{c} = {round(adj/c*100,1)}%')
