import json, glob, os

for pattern in [
    'reports/*fast_cooldown*events*',
    'reports/*dds50*events*',
    'reports/*exitbreak*events*',
]:
    files = glob.glob(pattern)
    for f in files:
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
        name = os.path.basename(f)[:70]
        if c > 0:
            adj = c - mae3
            print(f'{name}: {c} closes, MAE<-3%:{mae3}, MAE<-5%:{mae5}, Worst:{worst:.3f}%, Adj WR:{adj}/{c}={round(adj/c*100,1)}%')
