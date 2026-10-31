"""
analysis/measure_volume_differences.py
--------------------------------------
Compute measured differences between reference setups (35) and extra_setups (298):
1. Level kind mix (OCL vs A vs V)
2. Key level age (days from formation to rejection)
3. Freshness (share fresh vs unfresh)
4. Rejection candle body direction agrees with bias (close vs open)
5. Distance between breakout level and rejected level (|bo - kl| / kl)
6. Number of levels rejected by the candle
7. HTF candle size ((high - low) / close)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.models import TriggerDirection, LevelKind

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")
extras_df = pd.read_csv(ROOT / "analysis" / "output" / "extra_setups.csv")

# 1. Measure Reference Setups
ref_metrics = []
for idx, r in ref_df.iterrows():
    sym = r["symbol"]
    rdir = r["dir"]
    pair = r["pair"]
    htf = "W" if "W1" in pair else "D"
    rej_t = pd.Timestamp(r["rej_open_utc"], tz="UTC")
    kl_price = float(r["kl_price"])
    bo_price = float(r["bo_price"])
    kl_kind = r["kl_kind"] # OCL, A, V
    
    # Load HTF candle
    df_htf = cache.load_candles(sym, htf)
    sub = df_htf[df_htf.index == rej_t]
    if not sub.empty:
        c_open = sub["open"].iloc[0]
        c_high = sub["high"].iloc[0]
        c_low = sub["low"].iloc[0]
        c_close = sub["close"].iloc[0]
        
        # Body direction
        # SELL agrees if close < open; BUY agrees if close > open
        body_agrees = (c_close < c_open) if rdir == "SELL" else (c_close > c_open)
        htf_size_pct = (c_high - c_low) / c_close * 100.0
    else:
        body_agrees = None
        htf_size_pct = None
        
    # Distance bo to kl
    dist_pct = abs(bo_price - kl_price) / kl_price * 100.0
    
    # Triggers on this candle to get age, freshness, num levels
    ac = "crypto" if "BTC" in sym or "ETH" in sym else ("commodities" if sym in ("USOIL", "XAGUSD") else "forex")
    lookback = 104 if htf == "W" else 250
    trigs = detect_triggers(df_htf, htf, sym, ac, lookback=lookback, max_disrespects=3, ocl_max_disrespects=3)
    matching = [t for t in trigs if t.trigger_candle_time == rej_t and (t.direction == TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH)]
    
    age_days = None
    fresh = True # reference setups are fresh
    num_lvls = 1
    if matching:
        t = matching[0]
        num_lvls = len(t.all_rejected_levels)
        if t.primary_level:
            age_days = (rej_t - t.primary_level.formation_time).total_seconds() / 86400.0
            fresh = t.primary_level.fresh
            
    ref_metrics.append({
        "kind": "OCL" if "OCL" in kl_kind else kl_kind,
        "age_days": age_days,
        "fresh": fresh,
        "body_agrees": body_agrees,
        "dist_pct": dist_pct,
        "num_lvls": num_lvls,
        "htf_size_pct": htf_size_pct,
    })

# 2. Measure Extra Setups
extra_metrics = []
for idx, r in extras_df.iterrows():
    sym = r["symbol"]
    rdir = r["direction"]
    htf = r["htf"]
    rej_t = pd.Timestamp(r["rej_time"], tz="UTC") if pd.notna(r["rej_time"]) and r["rej_time"] != "" else None
    kl_price = float(r["level_price"]) if pd.notna(r["level_price"]) and r["level_price"] != "" else None
    bo_price = float(r["bo_price"])
    kl_kind_raw = str(r["level_kind"])
    kl_kind = "OCL" if "OCL" in kl_kind_raw else kl_kind_raw
    fresh = bool(r["fresh"])
    
    body_agrees = None
    htf_size_pct = None
    age_days = None
    num_lvls = 1
    dist_pct = None
    
    if kl_price:
        dist_pct = abs(bo_price - kl_price) / kl_price * 100.0
        
    if rej_t:
        df_htf = cache.load_candles(sym, htf)
        sub = df_htf[df_htf.index == rej_t]
        if not sub.empty:
            c_open = sub["open"].iloc[0]
            c_high = sub["high"].iloc[0]
            c_low = sub["low"].iloc[0]
            c_close = sub["close"].iloc[0]
            body_agrees = (c_close < c_open) if (rdir == "bearish" or rdir == "SELL") else (c_close > c_open)
            htf_size_pct = (c_high - c_low) / c_close * 100.0
            
        ac = "crypto" if "BTC" in sym or "ETH" in sym else ("commodities" if sym in ("USOIL", "XAGUSD") else "forex")
        lookback = 104 if htf == "W" else 250
        trigs = detect_triggers(df_htf, htf, sym, ac, lookback=lookback, max_disrespects=3, ocl_max_disrespects=3)
        exp_dir = TriggerDirection.BEARISH if (rdir == "bearish" or rdir == "SELL") else TriggerDirection.BULLISH
        matching = [t for t in trigs if t.trigger_candle_time == rej_t and t.direction == exp_dir]
        if matching:
            t = matching[0]
            num_lvls = len(t.all_rejected_levels)
            if t.primary_level:
                age_days = (rej_t - t.primary_level.formation_time).total_seconds() / 86400.0
                
    extra_metrics.append({
        "kind": kl_kind,
        "age_days": age_days,
        "fresh": fresh,
        "body_agrees": body_agrees,
        "dist_pct": dist_pct,
        "num_lvls": num_lvls,
        "htf_size_pct": htf_size_pct,
    })

df_ref_m = pd.DataFrame(ref_metrics)
df_ext_m = pd.DataFrame(extra_metrics)

print("=" * 80)
print("MEASURED DIFFERENCES: REFERENCE SETUPS (35) vs EXTRA SETUPS (298)")
print("=" * 80)

# 1. Level Kind Mix
print("\n1. LEVEL KIND MIX:")
print("Reference Setups (35):")
print(df_ref_m["kind"].value_counts(normalize=True).round(3) * 100)
print("Extra Setups (298):")
print(df_ext_m["kind"].value_counts(normalize=True).round(3) * 100)

# 2. Key Level Age (days)
print("\n2. KEY LEVEL AGE (days from formation to rejection):")
ref_age = df_ref_m["age_days"].dropna()
ext_age = df_ext_m["age_days"].dropna()
print(f"Reference: Mean = {ref_age.mean():.1f} d, Median = {ref_age.median():.1f} d, Max = {ref_age.max():.1f} d")
print(f"Extras:    Mean = {ext_age.mean():.1f} d, Median = {ext_age.median():.1f} d, Max = {ext_age.max():.1f} d")

# 3. Freshness Share
print("\n3. FRESHNESS:")
print(f"Reference: {df_ref_m['fresh'].mean() * 100:.1f}% fresh")
print(f"Extras:    {df_ext_m['fresh'].mean() * 100:.1f}% fresh")

# 4. Rejection Candle Body Agrees with Bias
print("\n4. REJECTION CANDLE BODY DIRECTION AGREES WITH BIAS:")
ref_body = df_ref_m["body_agrees"].dropna()
ext_body = df_ext_m["body_agrees"].dropna()
print(f"Reference: {ref_body.mean() * 100:.1f}% agree (close moved in setup direction)")
print(f"Extras:    {ext_body.mean() * 100:.1f}% agree (close moved in setup direction)")

# 5. Distance between Breakout Level and Rejected Level (% of price)
print("\n5. DISTANCE BETWEEN BREAKOUT LEVEL AND REJECTED LEVEL (% of price):")
ref_dist = df_ref_m["dist_pct"].dropna()
ext_dist = df_ext_m["dist_pct"].dropna()
print(f"Reference: Mean = {ref_dist.mean():.2f}%, Median = {ref_dist.median():.2f}%")
print(f"Extras:    Mean = {ext_dist.mean():.2f}%, Median = {ext_dist.median():.2f}%")

# 6. Number of Levels Rejected by the Candle
print("\n6. NUMBER OF LEVELS REJECTED BY CANDLE:")
ref_num = df_ref_m["num_lvls"].dropna()
ext_num = df_ext_m["num_lvls"].dropna()
print(f"Reference: Mean = {ref_num.mean():.2f}, Median = {ref_num.median():.1f}, >=2 lvls = {(ref_num >= 2).mean()*100:.1f}%")
print(f"Extras:    Mean = {ext_num.mean():.2f}, Median = {ext_num.median():.1f}, >=2 lvls = {(ext_num >= 2).mean()*100:.1f}%")

# 7. HTF Candle Size (% range)
print("\n7. HTF REJECTION CANDLE SIZE (% range):")
ref_size = df_ref_m["htf_size_pct"].dropna()
ext_size = df_ext_m["htf_size_pct"].dropna()
print(f"Reference: Mean = {ref_size.mean():.2f}%, Median = {ref_size.median():.2f}%")
print(f"Extras:    Mean = {ext_size.mean():.2f}%, Median = {ext_size.median():.2f}%")
