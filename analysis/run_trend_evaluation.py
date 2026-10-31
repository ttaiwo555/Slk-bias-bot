"""
analysis/run_trend_evaluation.py
--------------------------------
Evaluate Trend Candidates (a), (b), (c) against:
1. 35 canonical reference setups
2. Extra setups from analysis/output/extra_setups.csv
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import detect_formation
from slk_bot.strategy.models import LevelKind, TriggerDirection

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")
extras_df = pd.read_csv(ROOT / "analysis" / "output" / "extra_setups.csv")

# Marginal thresholds
MARGINAL_THRESHOLDS = {
    "forex": 0.0003,      # 3 pips
    "indices": 0.0010,    # 0.10%
    "crypto": 0.0010,     # 0.10%
    "commodities": 0.0010,# 0.10%
}

def get_asset_class(sym: str) -> str:
    if "BTC" in sym or "ETH" in sym or "SOL" in sym:
        return "crypto"
    if sym in ("NAS100", "SPX500", "US30", "GER40", "UK100", "EU50", "JPN225"):
        return "indices"
    if sym in ("XAUUSD", "XAGUSD", "USOIL", "BRENT"):
        return "commodities"
    return "forex"

# Candidate (a): Last broken A-shape (up) or V-shape (down)
def compute_trend_a(df: pd.DataFrame):
    closes = df["close"].values
    opens = df["open"].values
    times = df.index
    n = len(df)
    
    levels = []
    for k in range(1, n):
        f = detect_formation(opens[k-1], closes[k-1], opens[k], closes[k], 0.0)
        if f:
            kind, role, side = f
            if kind in (LevelKind.A_SHAPE, LevelKind.V_SHAPE):
                levels.append((k, kind, float(closes[k-1])))
                
    # Track breaks
    breaks = []
    for k in range(1, n):
        c = closes[k]
        t = times[k]
        for conf_k, kind, p in levels:
            if conf_k < k:
                if kind == LevelKind.A_SHAPE and c > p:
                    breaks.append((t, "BULLISH"))
                elif kind == LevelKind.V_SHAPE and c < p:
                    breaks.append((t, "BEARISH"))
    return breaks

# Candidate (b): Protected swing rule (alternating peaks/troughs on close chart)
def compute_trend_b(df: pd.DataFrame, use_marginal: bool = False, sym: str = ""):
    closes = df["close"].values
    opens = df["open"].values
    times = df.index
    n = len(df)
    
    # 1. Detect all confirmed A and V shapes
    # A-shape = peak at k-1, confirmed at k
    # V-shape = trough at k-1, confirmed at k
    swings = []
    for k in range(1, n):
        f = detect_formation(opens[k-1], closes[k-1], opens[k], closes[k], 0.0)
        if f:
            kind, role, side = f
            if kind == LevelKind.A_SHAPE:
                swings.append((k, "PEAK", float(closes[k-1]), times[k-1]))
            elif kind == LevelKind.V_SHAPE:
                swings.append((k, "TROUGH", float(closes[k-1]), times[k-1]))
                
    ac = get_asset_class(sym)
    thresh = MARGINAL_THRESHOLDS.get(ac, 0.0005)
    
    # Trend state chronologically
    trend_state = "NONE"
    state_history = [] # list of (time, state)
    
    # We walk through candles k
    for k in range(1, n):
        c = closes[k]
        t = times[k]
        
        # Swings confirmed at or before k
        avail_swings = [s for s in swings if s[0] <= k]
        if not avail_swings:
            continue
            
        # Find latest trough and the peak that preceded it
        # Find latest peak and the trough that preceded it
        troughs = [s for s in avail_swings if s[1] == "TROUGH"]
        peaks = [s for s in avail_swings if s[1] == "PEAK"]
        
        # Flip to UP: candle closes above the peak that preceded the most recent trough
        # Flip to DOWN: candle closes below the trough that preceded the most recent peak
        flip_up = False
        flip_down = False
        
        if troughs:
            last_trough = troughs[-1]
            # Peak that preceded it
            prior_peaks = [p for p in peaks if p[0] < last_trough[0]]
            if prior_peaks:
                target_peak = prior_peaks[-1]
                req = target_peak[2] * (1.0 + thresh) if use_marginal else target_peak[2]
                if c > req:
                    flip_up = True
                    
        if peaks:
            last_peak = peaks[-1]
            # Trough that preceded it
            prior_troughs = [tr for tr in troughs if tr[0] < last_peak[0]]
            if prior_troughs:
                target_trough = prior_troughs[-1]
                req = target_trough[2] * (1.0 - thresh) if use_marginal else target_trough[2]
                if c < req:
                    flip_down = True
                    
        if flip_up and not flip_down:
            trend_state = "BULLISH"
        elif flip_down and not flip_up:
            trend_state = "BEARISH"
            
        state_history.append((t, trend_state))
        
    return state_history

_CACHE_A = {}
_CACHE_B = {}
_CACHE_C = {}

def get_trend(sym: str, tf: str, asof: pd.Timestamp, candidate: str):
    key = (sym, tf)
    if candidate == "a":
        if key not in _CACHE_A:
            df = cache.load_candles(sym, tf)
            _CACHE_A[key] = compute_trend_a(df) if not df.empty else []
        breaks = _CACHE_A[key]
        prior = [b for b in breaks if b[0] <= asof]
        return prior[-1][1] if prior else "NONE"
        
    elif candidate == "b":
        if key not in _CACHE_B:
            df = cache.load_candles(sym, tf)
            _CACHE_B[key] = compute_trend_b(df, False, sym) if not df.empty else []
        hist = _CACHE_B[key]
        prior = [h for h in hist if h[0] <= asof]
        return prior[-1][1] if prior else "NONE"
        
    elif candidate == "c":
        if key not in _CACHE_C:
            df = cache.load_candles(sym, tf)
            _CACHE_C[key] = compute_trend_b(df, True, sym) if not df.empty else []
        hist = _CACHE_C[key]
        prior = [h for h in hist if h[0] <= asof]
        return prior[-1][1] if prior else "NONE"

def evaluate_candidates():
    for cand_name, cand_code in [
        ("Candidate (a): Last broken A/V shape", "a"),
        ("Candidate (b): Protected swing rule (alternating joints)", "b"),
        ("Candidate (c): Protected swing rule with MARGINAL threshold", "c"),
    ]:
        print("=" * 80)
        print(f"EVALUATING: {cand_name}")
        print("=" * 80)
        
        # 1. Reference Setups (35)
        aligned_ref = 0
        against_ref = 0
        none_ref = 0
        unaligned_setups = []
        
        for idx, r in ref_df.iterrows():
            sym = r["symbol"]
            rdir = r["dir"]
            exp_trend = "BULLISH" if rdir == "BUY" else "BEARISH"
            pair = r["pair"]
            tf = "W" if "W1" in pair else "D"
            t_rej = pd.Timestamp(r["rej_open_utc"], tz="UTC")
            
            t_dir = get_trend(sym, tf, t_rej, cand_code)
            if t_dir == exp_trend:
                aligned_ref += 1
            elif t_dir == "NONE":
                none_ref += 1
                unaligned_setups.append((r["id"], sym, rdir, t_dir, str(t_rej)[:10]))
            else:
                against_ref += 1
                unaligned_setups.append((r["id"], sym, rdir, t_dir, str(t_rej)[:10]))
                
        pct_ref = aligned_ref / len(ref_df) * 100
        print(f"Reference Setups (35 total):")
        print(f"  Aligned: {aligned_ref} / 35 ({pct_ref:.1f}%)")
        print(f"  Against: {against_ref} / 35")
        print(f"  None:    {none_ref} / 35")
        if unaligned_setups:
            print(f"  Unaligned Setups ({len(unaligned_setups)}):")
            for u in unaligned_setups:
                print(f"    # {u[0]}: {u[1]} {u[2]} (Engine Trend: {u[3]}, Rej: {u[4]})")
                
        # 2. Extra Setups
        # An extra setup is removed by trend alignment if its trend is AGAINST or NONE
        removed_extras = 0
        total_extras = len(extras_df)
        
        for idx, r in extras_df.iterrows():
            sym = r["symbol"]
            rdir = r["direction"]
            exp_trend = "BULLISH" if rdir == "bullish" or rdir == "BUY" else "BEARISH"
            htf = r["htf"]
            t_rej = pd.Timestamp(r["rej_time"], tz="UTC")
            
            t_dir = get_trend(sym, htf, t_rej, cand_code)
            if t_dir != exp_trend:
                removed_extras += 1
                
        pct_removed = removed_extras / total_extras * 100 if total_extras > 0 else 0
        print(f"\nExtra Setups ({total_extras} total):")
        print(f"  Removed by Trend Filter: {removed_extras} / {total_extras} ({pct_removed:.1f}%)")
        print(f"  Remaining Extras:        {total_extras - removed_extras}")
        print()

if __name__ == "__main__":
    evaluate_candidates()
