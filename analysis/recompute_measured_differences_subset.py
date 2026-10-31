"""
analysis/recompute_measured_differences_subset.py
-------------------------------------------------
Recomputes measured differences using ONLY reference rows where our key level
matches the reference level within 0.03%.
Reports:
  - Exact count of matching rows
  - Comparison of features against extra setups
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.models import TriggerDirection

cache = CandleCache()
match_df = pd.read_csv(ROOT / "analysis" / "output" / "match_table.csv")
ref_df = pd.read_csv(ROOT / "reference_setups.csv")
extras_df = pd.read_csv(ROOT / "analysis" / "output" / "extra_setups.csv")

# Filter reference rows where our key level matches within 0.03%
subset_rows = []
for idx, r in ref_df.iterrows():
    m = match_df[match_df["id"] == r["id"]]
    if not m.empty and m.iloc[0]["status"] == "MATCH":
        r_p = float(r["kl_price"])
        o_p = float(m.iloc[0]["our_level_price"])
        diff_pct = abs(o_p - r_p) / r_p * 100.0
        if diff_pct <= 0.03:
            subset_rows.append(r)

df_subset_ref = pd.DataFrame(subset_rows)
print(f"Total reference rows matching within 0.03%: {len(df_subset_ref)} / {len(ref_df)}")

# Measure metrics on this subset
subset_metrics = []
for idx, r in df_subset_ref.iterrows():
    sym = r["symbol"]
    rdir = r["dir"]
    pair = r["pair"]
    htf = "W" if "W1" in pair else "D"
    rej_t = pd.Timestamp(r["rej_open_utc"], tz="UTC")
    kl_price = float(r["kl_price"])
    bo_price = float(r["bo_price"])
    kl_kind = r["kl_kind"]
    
    df_htf = cache.load_candles(sym, htf)
    sub = df_htf[df_htf.index == rej_t]
    if not sub.empty:
        c_open = sub["open"].iloc[0]
        c_high = sub["high"].iloc[0]
        c_low = sub["low"].iloc[0]
        c_close = sub["close"].iloc[0]
        body_agrees = (c_close < c_open) if rdir == "SELL" else (c_close > c_open)
        htf_size_pct = (c_high - c_low) / c_close * 100.0
    else:
        body_agrees = None
        htf_size_pct = None
        
    dist_pct = abs(bo_price - kl_price) / kl_price * 100.0
    
    # Trigger details
    trigs = detect_triggers(
        df_htf, htf, sym, "forex" if sym not in ("SPX500", "GER40") else "indices",
        lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first"
    )
    exp_dir = TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH
    matching = [t for t in trigs if t.trigger_candle_time == rej_t and t.direction == exp_dir]
    num_lvls = 1
    fresh = True
    if matching:
        t = matching[0]
        num_lvls = len(t.all_rejected_levels)
        if t.primary_level:
            fresh = t.primary_level.fresh
            
    subset_metrics.append({
        "kind": "OCL" if "OCL" in kl_kind else kl_kind,
        "fresh": fresh,
        "body_agrees": body_agrees,
        "dist_pct": dist_pct,
        "num_lvls": num_lvls,
        "htf_size_pct": htf_size_pct,
    })

df_sub_m = pd.DataFrame(subset_metrics)

# Measure on extras
extras_metrics = []
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
    num_lvls = 1
    dist_pct = abs(bo_price - kl_price) / kl_price * 100.0 if kl_price else None
    
    if rej_t:
        df_htf = cache.load_candles(sym, htf)
        sub = df_htf[df_htf.index == rej_t]
        if not sub.empty:
            c_open = sub["open"].iloc[0]
            c_high = sub["high"].iloc[0]
            c_low = sub["low"].iloc[0]
            c_close = sub["close"].iloc[0]
            body_agrees = (c_close < c_open) if (rdir in ("bearish", "SELL")) else (c_close > c_open)
            htf_size_pct = (c_high - c_low) / c_close * 100.0
        trigs = detect_triggers(
            df_htf, htf, sym, "forex" if sym not in ("SPX500", "GER40") else "indices",
            lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first"
        )
        exp_dir = TriggerDirection.BEARISH if (rdir in ("bearish", "SELL")) else TriggerDirection.BULLISH
        matching = [t for t in trigs if t.trigger_candle_time == rej_t and t.direction == exp_dir]
        if matching:
            num_lvls = len(matching[0].all_rejected_levels)
            
    extras_metrics.append({
        "kind": kl_kind,
        "fresh": fresh,
        "body_agrees": body_agrees,
        "dist_pct": dist_pct,
        "num_lvls": num_lvls,
        "htf_size_pct": htf_size_pct,
    })

df_ext_m = pd.DataFrame(extras_metrics)

print("=" * 80)
print(f"MEASURED DIFFERENCES: EXACT MATCHING SUBSET ({len(df_sub_m)} setups) vs EXTRAS ({len(df_ext_m)} setups)")
print("=" * 80)

# 1. Level Kind Mix
print("\n1. LEVEL KIND MIX:")
print("Subset Reference Setups:")
print(df_sub_m["kind"].value_counts(normalize=True).round(3) * 100)
print("Extra Setups:")
print(df_ext_m["kind"].value_counts(normalize=True).round(3) * 100)

# 2. Freshness Share
print("\n2. FRESHNESS SHARE:")
print(f"Subset Reference: {df_sub_m['fresh'].mean() * 100:.1f}% fresh")
print(f"Extras:           {df_ext_m['fresh'].mean() * 100:.1f}% fresh")

# 3. Body Agrees with Bias
print("\n3. REJECTION BODY DIRECTION AGREES WITH BIAS:")
sub_b = df_sub_m["body_agrees"].dropna()
ext_b = df_ext_m["body_agrees"].dropna()
print(f"Subset Reference: {sub_b.mean() * 100:.1f}% agree")
print(f"Extras:           {ext_b.mean() * 100:.1f}% agree")

# 4. Distance between BO level and rejected level
print("\n4. DISTANCE BO TO REJECTED LEVEL (% of price):")
sub_d = df_sub_m["dist_pct"].dropna()
ext_d = df_ext_m["dist_pct"].dropna()
print(f"Subset Reference: Mean = {sub_d.mean():.2f}%, Median = {sub_d.median():.2f}%")
print(f"Extras:           Mean = {ext_d.mean():.2f}%, Median = {ext_d.median():.2f}%")

# 5. Number of Levels Rejected
print("\n5. NUMBER OF LEVELS REJECTED BY CANDLE:")
sub_n = df_sub_m["num_lvls"].dropna()
ext_n = df_ext_m["num_lvls"].dropna()
print(f"Subset Reference: Mean = {sub_n.mean():.2f}, Median = {sub_n.median():.1f}, >=2 lvls = {(sub_n >= 2).mean()*100:.1f}%")
print(f"Extras:           Mean = {ext_n.mean():.2f}, Median = {ext_n.median():.1f}, >=2 lvls = {(ext_n >= 2).mean()*100:.1f}%")

# 6. HTF Candle Size
print("\n6. HTF REJECTION CANDLE SIZE (% range):")
sub_s = df_sub_m["htf_size_pct"].dropna()
ext_s = df_ext_m["htf_size_pct"].dropna()
print(f"Subset Reference: Mean = {sub_s.mean():.2f}%, Median = {sub_s.median():.2f}%")
print(f"Extras:           Mean = {ext_s.mean():.2f}%, Median = {ext_s.median():.2f}%")
print("=" * 80)
