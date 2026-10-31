import sys
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    BreakoutType,
    SLKAlert,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod
from analysis.evaluate_trend_three_moments import get_trend_at_time

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

# Date range: 12 Oct 2026 00:00 to 16 Oct 2026 23:59:59 UTC
START_WEEK = pd.Timestamp("2026-10-12 00:00:00", tz="UTC")
END_WEEK = pd.Timestamp("2026-10-16 23:59:59", tz="UTC")

with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
    inst_data = yaml.safe_load(f)

in_scope_symbols = set(ref_df["symbol"].unique())
instruments = [inst for inst in inst_data.get("instruments", []) if inst["name"] in in_scope_symbols]

# Load all alerts under parity settings
all_raw_alerts = []

for inst in instruments:
    sym = inst["name"]
    ac = inst.get("asset_class", "forex")

    # Daily -> H4
    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    if not df_d.empty and not df_h4.empty:
        trigs_d = detect_triggers(
            df_d, "D", sym, ac,
            lookback=None, max_disrespects=None, ocl_max_disrespects=None,
            primary_selection_rule="fresh_first",
        )
        h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
        for t in trigs_d:
            if t.method == TriggerMethod.METHOD_2:
                continue
            if t.trigger_candle_time < pd.Timestamp("2026-09-20", tz="UTC"):
                continue
            s = evaluate_setup_lifecycle(
                t, df_d, df_h4, df_d, sym, "D", "H4",
                unfresh_alerts=True, all_ltf_levels=h4_levels,
                external_ref_point="first_touch",
            )
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL and START_WEEK <= a.breakout_candle_time <= END_WEEK:
                    all_raw_alerts.append((a, s, df_d, "D"))

    # Weekly -> Daily
    df_w = cache.load_candles(sym, "W")
    if not df_w.empty and not df_d.empty:
        trigs_w = detect_triggers(
            df_w, "W", sym, ac,
            lookback=None, max_disrespects=None, ocl_max_disrespects=None,
            primary_selection_rule="fresh_first",
        )
        d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
        for t in trigs_w:
            if t.method == TriggerMethod.METHOD_2:
                continue
            if t.trigger_candle_time < pd.Timestamp("2026-08-01", tz="UTC"):
                continue
            s = evaluate_setup_lifecycle(
                t, df_w, df_d, df_d, sym, "W", "D",
                unfresh_alerts=True, all_ltf_levels=d_levels,
                external_ref_point="first_touch",
            )
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL and START_WEEK <= a.breakout_candle_time <= END_WEEK:
                    all_raw_alerts.append((a, s, df_w, "W"))

print(f"Total raw external alerts in week: {len(all_raw_alerts)}")

# User's counting rule filtering:
# - Method 1 counts only with a FRESH primary level
# - Method 3 counts with any level
# - sweep-only off (already done)
# - trend required (candidate c, evaluated at alert time as default)
# - external breakouts only (already done)
filtered_alerts = []
for a, s, df_htf, tf in all_raw_alerts:
    # 1. Method filter:
    is_fresh = a.htf_level.fresh if a.htf_level else False
    if a.method == TriggerMethod.METHOD_1:
        if not is_fresh:
            continue
    elif a.method == TriggerMethod.METHOD_3:
        pass  # Method 3 counts with any level
    else:
        continue  # Method 2 excluded

    # 2. Trend filter (Candidate c evaluated at alert time)
    # At alert time: use D1 (or W1) candles closed up to a.breakout_candle_time
    prior_htf = df_htf[df_htf.index < a.breakout_candle_time]
    asof = prior_htf.index[-1] if not prior_htf.empty else a.breakout_candle_time
    trend, _ = get_trend_at_time(a.symbol, tf, asof, "c")
    exp_trend = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
    if trend != exp_trend:
        continue

    filtered_alerts.append({
        "alert": a,
        "setup": s,
        "symbol": a.symbol,
        "direction": a.direction.value,
        "method": int(a.method),
        "fresh": is_fresh,
        "bo_time": a.breakout_candle_time,
        "weekday": a.breakout_candle_time.day_name(),
        "htf": tf,
        "level_price": a.htf_level.price if a.htf_level else None,
        "bo_price": a.broken_level.price,
    })

df_filtered = pd.DataFrame(filtered_alerts)
print(f"Filtered alerts under user's counting rule: {len(df_filtered)}")

# Reference setups breakdown by weekday
ref_df["bo_dt"] = pd.to_datetime(ref_df["bo_open_utc"])
ref_df["weekday"] = ref_df["bo_dt"].dt.day_name()
ref_counts_by_day = ref_df["weekday"].value_counts().to_dict()

# Unique setups per symbol, direction, day
df_unique = df_filtered.drop_duplicates(subset=["symbol", "direction", "weekday"])

days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

print("\n" + "=" * 90)
print("VOLUME BREAKDOWN PER WEEKDAY (12-16 OCT 2026)")
print("Counting Rule: Method 1 Fresh-Only, Method 3 Any, Trend Required (c), Parity Settings")
print("=" * 90)
print(f"{'Weekday':<11} | {'Total Unique':>12} | {'Fresh':>8} | {'Unfresh (M3)':>13} | {'Reference':>10}")
print("-" * 65)
for day in days_order:
    sub_day = df_unique[df_unique["weekday"] == day]
    u_tot = len(sub_day)
    u_fresh = len(sub_day[sub_day["fresh"] == True])
    u_unfresh = len(sub_day[sub_day["fresh"] == False])
    ref_cnt = ref_counts_by_day.get(day, 0)
    print(f"{day:<11} | {u_tot:>12} | {u_fresh:>8} | {u_unfresh:>13} | {ref_cnt:>10}")
print("-" * 65)
print(f"{'Total':<11} | {len(df_unique):>12} | {len(df_unique[df_unique['fresh']==True]):>8} | {len(df_unique[df_unique['fresh']==False]):>13} | {len(ref_df):>10}")
print("=" * 90)

# Per-instrument counts
print("\nPER-INSTRUMENT UNIQUE COUNTS:")
inst_counts = df_unique["symbol"].value_counts().to_dict()
ref_inst_counts = ref_df["symbol"].value_counts().to_dict()
all_syms = sorted(in_scope_symbols)
print(f"{'Symbol':<10} | {'Our Unique Setups':>18} | {'Ref Setups':>12} | {'Extras':>8}")
print("-" * 55)
total_extras = 0
for sym in all_syms:
    ours = inst_counts.get(sym, 0)
    refs = ref_inst_counts.get(sym, 0)
    diff = max(0, ours - refs)
    total_extras += diff
    print(f"{sym:<10} | {ours:>18} | {refs:>12} | {diff:>8}")
print("-" * 55)
print(f"{'Total':<10} | {len(df_unique):>18} | {len(ref_df):>12} | {total_extras:>8}")

# Check how many reference setups are reproduced with same breakout candle
match_ref_count = 0
for idx, r in ref_df.iterrows():
    sym = r["symbol"]
    rdir = r["dir"]
    exp_dir = "BULLISH" if rdir == "BUY" else "BEARISH"
    bo_t = pd.Timestamp(r["bo_open_utc"], tz="UTC")
    # Check if in df_filtered
    match = df_filtered[
        (df_filtered["symbol"] == sym) &
        (df_filtered["direction"] == exp_dir.lower()) &
        (df_filtered["bo_time"] == bo_t)
    ]
    if not match.empty:
        match_ref_count += 1

print(f"\nReference setups reproduced with same breakout candle: {match_ref_count} / {len(ref_df)}")

# -------------------------------------------------------------------------
# Test information-only rule: One Active Setup per Instrument and Direction
# Suppress a new alert for the same instrument and direction while earlier setup
# is not yet invalidated or expired.
# -------------------------------------------------------------------------
print("\n" + "=" * 90)
print("TESTING INFORMATION-ONLY RULE: ONE ACTIVE SETUP PER INSTRUMENT & DIRECTION")
print("=" * 90)

# Sort filtered alerts chronologically
df_filtered_sorted = df_filtered.sort_values(by="bo_time").copy()

suppressed_alerts = []
active_setups = {} # (symbol, direction) -> expiry_time

for idx, row in df_filtered_sorted.iterrows():
    key = (row["symbol"], row["direction"])
    t_alert = row["bo_time"]
    s = row["setup"]

    # Check if there is an active setup for this symbol and direction
    if key in active_setups:
        expiry = active_setups[key]
        if t_alert < expiry:
            # Suppress!
            continue

    # Setup accepted!
    suppressed_alerts.append(row)
    active_setups[key] = s.timeline.expiry_time

df_suppressed = pd.DataFrame(suppressed_alerts)
df_suppressed_unique = df_suppressed.drop_duplicates(subset=["symbol", "direction", "weekday"])

print(f"{'Weekday':<11} | {'One-Active Unique':>18} | {'Reference':>10}")
print("-" * 45)
for day in days_order:
    cnt = len(df_suppressed_unique[df_suppressed_unique["weekday"] == day]) if not df_suppressed_unique.empty else 0
    ref_cnt = ref_counts_by_day.get(day, 0)
    print(f"{day:<11} | {cnt:>18} | {ref_cnt:>10}")
print("-" * 45)
tot_supp = len(df_suppressed_unique)
print(f"{'Total':<11} | {tot_supp:>18} | {len(ref_df):>10}")

# How many reference setups still reproduced under one-active rule?
match_ref_supp_count = 0
for idx, r in ref_df.iterrows():
    sym = r["symbol"]
    rdir = r["dir"]
    exp_dir = "BULLISH" if rdir == "BUY" else "BEARISH"
    bo_t = pd.Timestamp(r["bo_open_utc"], tz="UTC")
    match = df_suppressed[
        (df_suppressed["symbol"] == sym) &
        (df_suppressed["direction"] == exp_dir.lower()) &
        (df_suppressed["bo_time"] == bo_t)
    ]
    if not match.empty:
        match_ref_supp_count += 1

print(f"\nUnder One-Active Rule:")
print(f"  Reference setups reproduced with same breakout candle: {match_ref_supp_count} / {len(ref_df)}")
print(f"  Total unique setups in week: {len(df_suppressed_unique)}")
print(f"  Number of extras (unique - reproduced): {len(df_suppressed_unique) - match_ref_supp_count}")
