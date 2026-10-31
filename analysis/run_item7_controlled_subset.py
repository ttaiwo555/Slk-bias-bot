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

# 1. Matched subset: MATCH rows with key level diff <= 0.03%
matched_rows = []
for idx, r in ref_df.iterrows():
    m = match_df[match_df["id"] == r["id"]]
    if not m.empty and m.iloc[0]["status"] == "MATCH":
        r_p = float(r["kl_price"])
        o_p = float(m.iloc[0]["our_level_price"])
        diff_pct = abs(o_p - r_p) / r_p * 100.0
        if diff_pct <= 0.03:
            matched_rows.append(r)

df_ref_sub = pd.DataFrame(matched_rows)
ref_count = len(df_ref_sub)
controlled_syms = set(df_ref_sub["symbol"].unique())

print("=" * 80)
print(f"ITEM 7: CONTROLLED MATCHED-SUBSET COMPARISON (SAME INSTRUMENTS ONLY)")
print("=" * 80)
print(f"Total reference rows in matched subset (<= 0.03% KL diff): {ref_count} rows")
print(f"Instruments in matched subset ({len(controlled_syms)}): {sorted(controlled_syms)}")

# Filter extras to ONLY these controlled instruments
df_extras_controlled = extras_df[extras_df["symbol"].isin(controlled_syms)].copy()
extras_count = len(df_extras_controlled)
print(f"Total extras rows in matched subset instruments: {extras_count} rows")

# Compute metrics for Reference subset
ref_metrics = []
for idx, r in df_ref_sub.iterrows():
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

    trigs = detect_triggers(
        df_htf, htf, sym, "forex" if sym not in ("SPX500", "GER40", "US30", "NAS100") else "indices",
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

    ref_metrics.append({
        "kind": "OCL" if "OCL" in kl_kind else kl_kind,
        "fresh": fresh,
        "body_agrees": body_agrees,
        "dist_pct": dist_pct,
        "num_lvls": num_lvls,
        "htf_size_pct": htf_size_pct,
    })

df_ref_m = pd.DataFrame(ref_metrics)

# Compute metrics for Controlled Extras
extras_metrics = []
for idx, r in df_extras_controlled.iterrows():
    sym = r["symbol"]
    rdir = r["direction"]
    htf = r["htf"]
    rej_t = pd.Timestamp(r["rej_time"], tz="UTC") if pd.notna(r["rej_time"]) and r["rej_time"] != "" else None
    kl_price = float(r["level_price"]) if pd.notna(r["level_price"]) and r["level_price"] != "" else None
    bo_price = float(r["bo_price"])
    kl_kind = r["level_kind"]
    fresh = bool(r["fresh"])

    if kl_price is not None and kl_price > 0:
        dist_pct = abs(bo_price - kl_price) / kl_price * 100.0
    else:
        dist_pct = None

    body_agrees = None
    htf_size_pct = None
    if rej_t is not None:
        df_htf = cache.load_candles(sym, htf)
        sub = df_htf[df_htf.index == rej_t]
        if not sub.empty:
            c_open = sub["open"].iloc[0]
            c_high = sub["high"].iloc[0]
            c_low = sub["low"].iloc[0]
            c_close = sub["close"].iloc[0]
            body_agrees = (c_close < c_open) if rdir == "bearish" else (c_close > c_open)
            htf_size_pct = (c_high - c_low) / c_close * 100.0

    extras_metrics.append({
        "kind": "OCL" if "OCL" in str(kl_kind) else str(kl_kind),
        "fresh": fresh,
        "body_agrees": body_agrees,
        "dist_pct": dist_pct,
        "htf_size_pct": htf_size_pct,
    })

df_ext_m = pd.DataFrame(extras_metrics)

# Compare distributions
print("\n" + "=" * 80)
print(f"{'Feature / Metric':<35} | {'Reference Subset (N=' + str(ref_count) + ')':>22} | {'Controlled Extras (N=' + str(extras_count) + ')':>22}")
print("-" * 84)

# 1. Fresh Level %
ref_fresh_pct = df_ref_m["fresh"].mean() * 100.0
ext_fresh_pct = df_ext_m["fresh"].mean() * 100.0
print(f"{'Fresh Primary Level Rate':<35} | {ref_fresh_pct:>21.1f}% | {ext_fresh_pct:>21.1f}%")

# 2. Kind distribution
for k in ["OCL", "A", "V"]:
    ref_k_pct = (df_ref_m["kind"] == k).mean() * 100.0
    ext_k_pct = (df_ext_m["kind"] == k).mean() * 100.0
    print(f"{'  Level Kind: ' + k:<35} | {ref_k_pct:>21.1f}% | {ext_k_pct:>21.1f}%")

# 3. Body Agreement %
ref_body_pct = df_ref_m["body_agrees"].dropna().mean() * 100.0
ext_body_pct = df_ext_m["body_agrees"].dropna().mean() * 100.0
print(f"{'Rejection Body Agrees with Trade':<35} | {ref_body_pct:>21.1f}% | {ext_body_pct:>21.1f}%")

# 4. Distance to Breakout (Mean and Median)
ref_dist_mean = df_ref_m["dist_pct"].dropna().mean()
ref_dist_med = df_ref_m["dist_pct"].dropna().median()
ext_dist_mean = df_ext_m["dist_pct"].dropna().mean()
ext_dist_med = df_ext_m["dist_pct"].dropna().median()
print(f"{'Mean Distance KL to BO (% of price)':<35} | {ref_dist_mean:>21.2f}% | {ext_dist_mean:>21.2f}%")
print(f"{'Median Distance KL to BO (% of price)':<35} | {ref_dist_med:>21.2f}% | {ext_dist_med:>21.2f}%")

# 5. HTF Candle Size (% of price)
ref_size_mean = df_ref_m["htf_size_pct"].dropna().mean()
ext_size_mean = df_ext_m["htf_size_pct"].dropna().mean()
print(f"{'Mean Rejection Candle Range (%)':<35} | {ref_size_mean:>21.2f}% | {ext_size_mean:>21.2f}%")
print("=" * 80)
