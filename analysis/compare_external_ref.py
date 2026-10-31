"""
analysis/compare_external_ref.py
--------------------------------
Compare external_reference_point: first_touch vs extreme
across the 35 canonical reference setups.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    find_mitigation_candle,
    get_candle_close_time,
    BreakoutType,
    SLKAlert,
    SLKSetup,
)
from slk_bot.strategy.models import (
    TriggerDirection,
    TriggerMethod,
    LevelKind,
    KeyLevel,
)

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

def get_tolerance(symbol: str, price: float) -> float:
    if symbol in ("XAGUSD", "XAUUSD"):
        return price * 0.0015
    elif symbol in ("NAS100", "SPX500", "US30", "GER40", "UK100"):
        return price * 0.0020
    return price * 0.0005

def find_first_touch_candle(ltf_df, trigger_start, trigger_end, kl_price):
    sub = ltf_df[(ltf_df.index >= trigger_start) & (ltf_df.index < trigger_end)]
    if sub.empty:
        return None
    touches = sub[(sub["low"] <= kl_price + 1e-6) & (sub["high"] >= kl_price - 1e-6)]
    if not touches.empty:
        t_time = touches.index[0]
        loc = ltf_df.index.get_loc(t_time)
        if isinstance(loc, slice):
            loc = loc.start
        return int(loc)
    return None

def run_eval(mode: str, formation_strict_less: bool = False):
    # Pre-load data
    results = []
    
    with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
        inst_data = yaml.safe_load(f)
    in_scope_symbols = set(ref_df["symbol"].unique())
    instruments = [inst for inst in inst_data.get("instruments", []) if inst["name"] in in_scope_symbols]
    
    engine_setups = []
    for inst in instruments:
        sym = inst["name"]
        ac = inst.get("asset_class", "forex")
        df_d = cache.load_candles(sym, "D")
        df_h4 = cache.load_candles(sym, "H4")
        if not df_d.empty and not df_h4.empty:
            trigs = detect_triggers(df_d, "D", sym, ac, lookback=250, max_disrespects=3, ocl_max_disrespects=3)
            h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
            for t in trigs:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                if t.trigger_candle_time < pd.Timestamp("2026-09-20", tz="UTC"):
                    continue
                s = evaluate_setup_lifecycle(
                    trigger=t,
                    htf_df=df_d,
                    ltf_df=df_h4,
                    daily_df=df_d,
                    symbol=sym,
                    htf_tf="D",
                    ltf_tf="H4",
                    unfresh_alerts=True,
                    all_ltf_levels=h4_levels,
                    external_ref_point=mode,
                )
                engine_setups.append(s)
                
        df_w = cache.load_candles(sym, "W")
        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(df_w, "W", sym, ac, lookback=104, max_disrespects=3, ocl_max_disrespects=3)
            d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
            for t in trigs_w:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                if t.trigger_candle_time < pd.Timestamp("2026-08-01", tz="UTC"):
                    continue
                s = evaluate_setup_lifecycle(
                    trigger=t,
                    htf_df=df_w,
                    ltf_df=df_d,
                    daily_df=df_d,
                    symbol=sym,
                    htf_tf="W",
                    ltf_tf="D",
                    unfresh_alerts=True,
                    all_ltf_levels=d_levels,
                    external_ref_point=mode,
                )
                engine_setups.append(s)
                
    # Compare against ref_df
    exact_matches = 0
    bo_price_matches = 0
    bo_time_matches = 0
    
    for idx, r in ref_df.iterrows():
        sym = r["symbol"]
        rdir = r["dir"]
        exp_dir = TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH
        ref_rej_time = pd.Timestamp(r["rej_open_utc"], tz="UTC")
        ref_bo_time = pd.Timestamp(r["bo_open_utc"], tz="UTC")
        ref_kl_p = float(r["kl_price"])
        ref_bo_p = float(r["bo_price"])
        
        cands = [s for s in engine_setups if s.symbol == sym and s.trigger.direction == exp_dir and s.timeline.trigger_time == ref_rej_time]
        if not cands:
            continue
            
        best_s = None
        for s in cands:
            if s.trigger.primary_level and abs(s.trigger.primary_level.price - ref_kl_p) <= get_tolerance(sym, ref_kl_p):
                best_s = s
                break
        if not best_s:
            best_s = cands[0]
            
        ext_alerts = [a for a in best_s.alerts if a.breakout_type == BreakoutType.EXTERNAL]
        if not ext_alerts:
            continue
            
        # Check matches
        best_a = None
        for a in ext_alerts:
            p_m = abs(a.broken_level.price - ref_bo_p) <= get_tolerance(sym, ref_bo_p)
            t_m = (a.breakout_candle_time == ref_bo_time)
            if p_m and t_m:
                best_a = a
                break
            if p_m or t_m:
                best_a = a
        if not best_a:
            best_a = ext_alerts[0]
            
        p_m = abs(best_a.broken_level.price - ref_bo_p) <= get_tolerance(sym, ref_bo_p)
        t_m = (best_a.breakout_candle_time == ref_bo_time)
        kl_m = best_s.trigger.primary_level and abs(best_s.trigger.primary_level.price - ref_kl_p) <= get_tolerance(sym, ref_kl_p)
        
        if p_m:
            bo_price_matches += 1
        if t_m:
            bo_time_matches += 1
        if kl_m and p_m and t_m:
            exact_matches += 1
            
    print(f"Mode: {mode}")
    print(f"  Exact Matches (KL + BO price + BO time): {exact_matches} / 35")
    print(f"  BO Price Matches: {bo_price_matches} / 35")
    print(f"  BO Time Matches:  {bo_time_matches} / 35")

if __name__ == "__main__":
    pass
