#!/usr/bin/env python3
"""
Coinbase Universe Scanner — Phase 4 (FINAL)
=============================================
Scan the REMAINING 163 untested coins.

Total USD pairs: ~388
Phase 1: 49 coins (Phase 1)
Phase 2: 80 coins (top 80 untested)
Phase 3: 100 coins (next 100)
Phase 4: remaining 159 coins (FINAL)

Standardized test: RSI(4)<30 + 25% TP + No SL, 7 days, $48
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "coinbase_universe_scan_phase4_final.json"

ALREADY_TESTED = {
    "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "FARTCOIN-USD", "VIRTUAL-USD", "TRUMP-USD", "FET-USD", "CFG-USD", "DASH-USD", "IRYS-USD", "MON-USD", "SKL-USD",
    "VVV-USD", "LDO-USD", "STORJ-USD", "COMP-USD", "ARB-USD", "SOL-USD", "AVAX-USD", "MATIC-USD", "LINK-USD", "UNI-USD",
    "AAVE-USD", "MKR-USD", "SNX-USD", "CRV-USD", "SUSHI-USD", "GRT-USD", "IMX-USD", "OP-USD", "APT-USD", "SUI-USD",
    "SEI-USD", "TIA-USD", "INJ-USD", "RUNE-USD", "ATOM-USD", "NEAR-USD", "FTM-USD", "ALGO-USD", "FLOW-USD", "ICP-USD",
    "FIL-USD", "EOS-USD", "XLM-USD", "XTZ-USD", "EGLD-USD",
    "PEPE-USD", "MOG-USD", "BONK-USD", "SHIB-USD", "BNKR-USD", "FLOKI-USD", "TOSHI-USD", "NOM-USD", "PUMP-USD", "NOICE-USD",
    "DOGINME-USD", "PENGU-USD", "SPELL-USD", "TRU-USD", "NKN-USD", "ACS-USD", "XAN-USD", "TURBO-USD", "FIGHT-USD", "B3-USD",
    "IDEX-USD", "DOGE-USD", "MDT-USD", "AMP-USD", "GIGA-USD", "XCN-USD", "KEYCAT-USD", "TROLL-USD", "RLS-USD", "DEGEN-USD",
    "RSR-USD", "FLR-USD", "GST-USD", "TRIA-USD", "BLAST-USD", "ROSE-USD", "VET-USD", "FAI-USD", "VARA-USD", "VTHO-USD",
    "IMU-USD", "JASMY-USD", "XRP-USD", "HBAR-USD", "A8-USD", "BOBBOB-USD", "ADA-USD", "ZK-USD", "ANKR-USD", "TOWNS-USD",
    "WLFI-USD", "LINEA-USD", "SWELL-USD", "SUP-USD", "GWEI-USD", "USELESS-USD", "SPK-USD", "REZ-USD", "TNSR-USD", "ATH-USD",
    "KAT-USD", "ROBO-USD", "W-USD", "ACH-USD", "PIRATE-USD", "QI-USD", "DRIFT-USD", "ALT-USD", "L3-USD", "SENT-USD",
    "GHST-USD", "USDT-USD", "HONEY-USD", "ENA-USD", "PLUME-USD", "CELR-USD", "CORECHAIN-USD", "RECALL-USD", "ONDO-USD", "XYO-USD",
    # Phase 3 (100 coins)
    "WELL-USD", "SWFTC-USD", "SHPING-USD", "BIO-USD", "MET-USD", "AERO-USD", "CTSI-USD", "ZORA-USD", "SKR-USD", "ASM-USD",
    "ZAMA-USD", "FUN1-USD", "DOOD-USD", "FIS-USD", "T-USD", "POL-USD", "BEAM-USD", "NCT-USD", "VOXEL-USD", "ALEO-USD",
    "GMT-USD", "XPL-USD", "STRK-USD", "VELO-USD", "COTI-USD", "WLD-USD", "CRO-USD", "OXT-USD", "PYTH-USD", "PERP-USD",
    "SQD-USD", "RED-USD", "HFT-USD", "AVNT-USD", "COOKIE-USD", "OGN-USD", "MOODENG-USD", "KITE-USD", "SEAM-USD", "FIDA-USD",
    "KARRAT-USD", "ARPA-USD", "INX-USD", "RARI-USD", "POND-USD", "SPX-USD", "AZTEC-USD", "USDS-USD", "SAPIEN-USD", "AIOZ-USD",
    "DAI-USD", "BASED1-USD", "LRC-USD", "POPCAT-USD", "AST-USD", "MAMO-USD", "WIF-USD", "TREE-USD", "EDGE-USD", "SPA-USD",
    "AXL-USD", "SAND-USD", "SKY-USD", "RARE-USD", "SXT-USD", "BIGTIME-USD", "ZETA-USD", "KMNO-USD", "PRCL-USD", "OSMO-USD",
    "AERGO-USD", "MEZO-USD", "SAFE-USD", "C98-USD", "S-USD", "CHZ-USD", "FLOCK-USD", "KTA-USD", "TRUST-USD", "SUPER-USD",
    "JTO-USD", "PROMPT-USD", "MAGIC-USD", "KERNEL-USD", "BICO-USD", "FORT-USD", "BLZ-USD", "LRDS-USD", "PRL-USD", "MATH-USD",
    "G-USD", "MINA-USD", "THQ-USD", "DOLO-USD", "BAT-USD", "IO-USD", "RENDER-USD", "APE-USD", "00-USD", "SUKU-USD",
}


def compute_rsi(closes, period=4):
    if len(closes) < period + 1: return [50.0]*len(closes)
    deltas = [closes[i]-closes[i-1] for i in range(1,len(closes))]
    gains, losses = [d if d>0 else 0 for d in deltas[-period:]], [-d if d<0 else 0 for d in deltas[-period:]]
    avg_g, avg_l = sum(gains)/period, sum(losses)/period
    if avg_l>0: return 100-100/(1+avg_g/avg_l)
    return 100.0

def run_rsi_test(candles, rsi_period=4, os_thresh=30, tp_pct=0.25, max_hold=24, fee_bps=40, starting_cash=48.0):
    if len(candles)<rsi_period+20: return None
    fee_rate=fee_bps/10000.0; closes=[float(c["close"]) for c in candles]; rsi_vals=compute_rsi(closes,rsi_period)
    cash,in_position,position,trades=starting_cash,False,None,[]
    for i in range(rsi_period+10,len(candles)-1):
        c=candles[i]; h=float(c["high"]); l=float(c["low"]); cl=float(c["close"]); current_rsi=rsi_vals[i]
        if in_position and position:
            exit_p,exit_r=None,None
            if h>=position["entry"]*(1+tp_pct): exit_p,exit_r=position["entry"]*(1+tp_pct),"tp"
            elif (i-position["bar"])>=max_hold: exit_p,exit_r=cl,"timeout"
            if exit_p is not None:
                qty=position["qty"]; gross=(exit_p-position["entry"])*qty
                ef,xf=position["entry"]*qty*fee_rate,exit_p*qty*fee_rate; net=gross-ef-xf
                cash+=position["quote"]+net; trades.append({"net":net,"win":net>0}); in_position=False; position=None; continue
        if not in_position and cash>=10.0 and current_rsi<=os_thresh:
            deploy=cash*0.95; entry_fee=cl*(deploy/cl)*fee_rate; qty=(deploy-entry_fee)/cl
            if qty>0: cash-=deploy; position={"entry":cl,"qty":qty,"bar":i,"quote":deploy}; in_position=True
    if position: cash+=position["quote"]
    net=cash-starting_cash; wins=[t for t in trades if t["win"]]
    return {"net":round(net,2),"return_pct":round(net/starting_cash*100,1),"trades":len(trades),"wr":round(len(wins)/max(1,len(trades))*100,1)}

def fetch_chunked(client,pid,start,end,gran="FIVE_MINUTE"):
    cs,all_c,retries=start,[],0
    gsec=300*5
    while cs<end:
        # Limit chunk to 300 candles to stay under API limit of 350
        chunk_end=min(cs+300*gsec,end)
        try:
            r=client.market_candles(pid,start=cs,end=chunk_end,granularity=gran)
            if isinstance(r,dict):
                cands=r.get("candles",[])
            elif isinstance(r,list):
                cands=r
            else:
                cands=[]
            all_c.extend(cands); cs=chunk_end
            if not cands: break
            retries=0; time.sleep(0.15)
        except Exception as e:
            estr=str(e)
            if "429" in estr:
                retries+=1; time.sleep(min(2**retries,10))
            elif "400" in estr or "350" in estr or "INVALID" in estr:
                # Coin doesn't have enough history, return what we have
                break
            else:
                cs=chunk_end; time.sleep(0.3)
    if not all_c: return []
    try:
        def sort_key(c):
            if isinstance(c,dict): return int(c.get("start",c.get("time",0)))
            return 0
        all_c.sort(key=sort_key)
    except: pass
    return all_c

def main():
    client=CoinbaseAdvancedClient()
    print("="*80); print("  COINBASE UNIVERSE SCANNER — Phase 4 (FINAL)"); print("="*80)
    
    print("\nFetching products...")
    resp=client.list_products(get_all_products=True,product_type="SPOT"); products=resp.get("products",[])
    untested=[]
    for p in products:
        pid=p.get("product_id","")
        if pid.endswith("-USD") and p.get("status")=="online" and pid not in ALREADY_TESTED:
            untested.append({"id":pid,"vol":float(p.get("volume_24h",0) or 0)})
    untested.sort(key=lambda x:x["vol"],reverse=True)
    print(f"  Remaining untested: {len(untested)}")
    
    coins=[c["id"] for c in untested]
    print(f"  Testing {len(coins)} coins...")
    
    now=int(time.time()); start=now-7*24*3600; all_results=[]; scanned=skipped=errors=0
    
    for i,pid in enumerate(coins):
        scanned+=1
        print(f"\n  [{scanned}/{len(coins)}] {pid}...",end=" ",flush=True)
        try:
            candles=fetch_chunked(client,pid,start,now)
            if len(candles)<100: print(f"SKIP ({len(candles)}c)"); skipped+=1; continue
            r=run_rsi_test(candles)
            if r:
                r["coin"]=pid; all_results.append(r)
                if r["net"]>0: print(f"✅ ${r['net']:.2f} ({r['trades']}t, {r['wr']}%WR)")
                else: print(f"❌ ${r['net']:.2f} ({r['trades']}t, {r['wr']}%WR)")
            else: print("SKIP")
        except Exception as e: errors+=1; print(f"ERR: {str(e)[:50]}")
        time.sleep(0.3)
    
    all_results.sort(key=lambda r:r["net"],reverse=True)
    prof=[r for r in all_results if r["net"]>0]; lose=[r for r in all_results if r["net"]<=0]
    
    report={"generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"tested":scanned,"profitable":len(prof),"losing":len(lose),"all_results":all_results,"profitable_coins":prof}
    REPORT_PATH.parent.mkdir(parents=True,exist_ok=True); REPORT_PATH.write_text(json.dumps(report,indent=2),encoding="utf-8")
    
    print(f"\n{'='*80}"); print(f"  PHASE 4 FINAL — {scanned} coins"); print(f"{'='*80}")
    print(f"\n  Tested: {scanned} | Profitable: {len(prof)}/{len(all_results)} | Losing: {len(lose)}/{len(all_results)}")
    
    if prof:
        print(f"\n  {'Coin':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6}")
        print(f"  {'-'*50}")
        for r in prof[:30]: print(f"  {r['coin']:<20} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}%")
        print(f"\n  Total profit from Phase 4 winners: ${sum(r['net'] for r in prof):.2f}")
    
    # Combined ALL phases
    phase1_3_profitable=[
        ("RAVE-USD",123.21,256.7,28,71.4),("MOG-USD",56.48,117.7,17,70.6),("A8-USD",18.89,39.3,21,61.9),
        ("IDEX-USD",16.08,33.5,15,60.0),("BAL-USD",8.74,18.2,25,56.0),("DRIFT-USD",6.38,13.3,32,37.5),
        ("ALEPH-USD",6.31,13.1,17,47.1),("IOTX-USD",4.25,8.9,15,46.7),("BLUR-USD",3.58,7.5,20,35.0),
        ("SKL-USD",2.81,5.9,10,50.0),("DOGINME-USD",1.53,3.2,13,38.5),("ALT-USD",1.03,2.2,28,39.3),
        ("DEGEN-USD",0.97,2.0,12,50.0),("IRYS-USD",0.77,1.6,48,50.0),("VTHO-USD",0.38,0.8,12,50.0),
        ("ACS-USD",0.12,0.2,4,50.0),("LRDS-USD",9.36,19.5,18,55.6),("STRK-USD",7.99,16.6,9,66.7),
        ("MATH-USD",5.92,12.3,17,52.9),("KARRAT-USD",4.09,8.5,7,71.4),("PERP-USD",3.54,7.4,22,31.8),
        ("VOXEL-USD",2.78,5.8,7,57.1),("OSMO-USD",2.60,5.4,3,66.7),("ARPA-USD",2.01,4.2,4,75.0),
        ("FIS-USD",1.72,3.6,15,46.7),("FORT-USD",1.61,3.3,5,60.0),("T-USD",1.53,3.2,4,50.0),
        ("RARE-USD",1.52,3.2,14,42.9),("00-USD",1.27,2.6,2,50.0),("VELO-USD",1.06,2.2,12,66.7),
        ("AST-USD",0.40,0.8,8,75.0),("WELL-USD",0.26,0.5,10,50.0),("SUKU-USD",0.17,0.3,5,60.0),
        ("GMT-USD",0.11,0.2,7,57.1),
    ]
    
    all_p=phase1_3_profitable+[(r["coin"],r["net"],r["return_pct"],r["trades"],r["wr"]) for r in prof]
    all_p.sort(key=lambda x:x[1],reverse=True)
    
    print(f"\n{'='*80}")
    print(f"  COMPLETE UNIVERSE — {49+80+100+scanned} coins tested, {len(all_p)} profitable")
    print(f"{'='*80}")
    print(f"\n  ALL PROFITABLE COINS (top {min(40,len(all_p))}):")
    print(f"  {'Coin':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6}")
    print(f"  {'-'*50}")
    for c,n,rp,t,w in all_p[:40]: print(f"  {c:<20} ${n:>6.2f} {rp:>6.1f}% {t:>7} {w:>5.1f}%")
    
    total=sum(x[1] for x in all_p)
    print(f"\n  Total profit from ALL profitable coins: ${total:.2f}/7d = ${total/7*30:.2f}/month")
    print(f"  Report: {REPORT_PATH}")
    return 0

if __name__=="__main__": raise SystemExit(main())
