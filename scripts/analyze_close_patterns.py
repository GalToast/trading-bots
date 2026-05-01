import json, os, glob

# Find all state files
state_files = glob.glob('reports/*state.json', recursive=True)

# Key live lanes to check
lanes = {
    # Live lanes
    'penetration_lattice_live_source_state': 'FX rearm 941777',
    'penetration_lattice_live_momentum_alpha50_source_state': 'Momentum 941778',
    'penetration_lattice_live_ethusd_m15_warp_state': 'ETH M15 941782',
    'penetration_lattice_live_btcusd_m15_warp_state': 'BTC M15 941781',
    'penetration_lattice_live_btcusd_exc2_tight_exec_state': 'BTC exc2 941779',
    # FX M15 warp
    'penetration_lattice_shadow_gbpusd_m15_warp_state': 'GBPUSD M15 warp',
    'penetration_lattice_shadow_usdjpy_m15_warp_state': 'USDJPY M15 warp',
    'penetration_lattice_shadow_xauusd_m15_warp_state': 'XAUUSD M15',
    'penetration_lattice_shadow_audusd_m15_warp_state': 'AUDUSD M15',
    'penetration_lattice_shadow_eurusd_m15_warp_state': 'EURUSD M15',
    'penetration_lattice_shadow_nzdusd_m15_warp_state': 'NZDUSD M15',
    'penetration_lattice_shadow_usdcad_m15_warp_state': 'USDCAD M15',
    # FX M15 micro
    'penetration_lattice_shadow_gbpusd_m15_fxmicro_state': 'GBPUSD M15 micro',
    'penetration_lattice_shadow_eurusd_m15_fxmicro_state': 'EURUSD M15 micro',
    'penetration_lattice_shadow_nzdusd_m15_fxmicro_state': 'NZDUSD M15 micro',
}

rows = []
for key, label in lanes.items():
    candidates = glob.glob(f'reports/**/{key}*', recursive=True)
    if not candidates:
        candidates = glob.glob(f'reports/{key}*')
    for path in candidates:
        if 'corrupt' in path.lower() or 'pre_recovery' in path.lower() or 'poisoned' in path.lower():
            continue
        try:
            with open(path) as f:
                state = json.load(f)
            meta = state.get('metadata', {})
            sym_data = state.get('symbols', {})
            for sym_name, s in sym_data.items():
                closes = s.get('realized_closes', 0)
                net = s.get('realized_net_usd', 0)
                opens = len(s.get('open_tickets', []))
                step = s.get('base_step_px', 0)
                close_alpha = s.get('raw_close_alpha', meta.get('raw_close_alpha', 0))
                close_style = s.get('raw_close_style', meta.get('raw_close_style', ''))
                variant = s.get('variant', meta.get('raw_rearm_variant', ''))
                momentum_gate = s.get('momentum_gate', meta.get('raw_rearm_momentum_gate', False))
                cooldown = s.get('raw_rearm_cooldown_bars', 0)
                resets = s.get('anchor_resets', 0)
                # Compute $/close
                per_close = net / closes if closes > 0 else 0
                rows.append({
                    'lane': label,
                    'symbol': sym_name,
                    'file': os.path.basename(path),
                    'closes': closes,
                    'net': net,
                    'per_close': per_close,
                    'opens': opens,
                    'step': step,
                    'close_alpha': close_alpha,
                    'close_style': close_style,
                    'variant': variant,
                    'momentum_gate': momentum_gate,
                    'cooldown': cooldown,
                    'resets': resets,
                })
        except Exception as e:
            rows.append({
                'lane': label,
                'symbol': '?',
                'file': os.path.basename(path),
                'error': str(e),
            })

# Sort by $/close descending (winners first)
rows.sort(key=lambda r: r.get('per_close', -999), reverse=True)

with open('reports/close_pattern_analysis.txt', 'w') as f:
    f.write(f"{'Lane':30} {'Sym':10} {'Closes':>6} {'Net $':>10} {'$/close':>8} {'Opens':>5} {'Step':>8} {'Alpha':>5} {'Style':20} {'MomGate':>7} {'CD':>3} {'Resets':>6}\n")
    f.write('-' * 140 + '\n')
    for r in rows:
        if 'error' in r:
            f.write(f"{r['lane']:30} {r['symbol']:10} ERROR: {r['error']}\n")
        else:
            f.write(f"{r['lane']:30} {r['symbol']:10} {r['closes']:>6} ${r['net']:>9.2f} ${r['per_close']:>7.2f} {r['opens']:>5} {r['step']:>8.5f} {r['close_alpha']:>5.1f} {r['close_style']:20} {str(r['momentum_gate']):>7} {r['cooldown']:>3} {r['resets']:>6}\n")

print(open('reports/close_pattern_analysis.txt').read())
