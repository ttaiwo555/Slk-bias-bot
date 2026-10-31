"""
analysis/print_all_35_trend_states.py
-------------------------------------
Prints the trend state (Candidate c, evaluated at alert time) for ALL 35 reference setups
with the swing points used.
Identifies exactly which are unaligned and provides the detailed reason why.
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.evaluate_trend_three_moments import get_trend_at_time, cache

ref_df = pd.read_csv(ROOT / "reference_setups.csv")

print("=" * 120)
print("TREND STATE (CANDIDATE C AT ALERT TIME) FOR ALL 35 REFERENCE SETUPS")
print("=" * 120)
print(f"{'ID':<3} | {'Symbol':<8} | {'Setup Dir':<10} | {'Pair':<7} | {'Alert Time (UTC)':<17} | {'Trend State':<12} | {'Aligned?':<9} | {'Swing Points Used'}")
print("-" * 120)

unaligned_list = []
aligned_cnt = 0

for idx, r in ref_df.iterrows():
    r_id = r["id"]
    sym = r["symbol"]
    rdir = r["dir"]
    pair = r["pair"]
    tf = "W" if "W1" in pair else "D"
    t_bo = pd.Timestamp(r["bo_open_utc"], tz="UTC")
    exp_trend = "BULLISH" if rdir == "BUY" else "BEARISH"

    df_tf = cache.load_candles(sym, tf)
    prior = df_tf[df_tf.index <= t_bo]
    asof = prior.index[-1] if not prior.empty else t_bo

    trend, swings = get_trend_at_time(sym, tf, asof, "c")
    is_aligned = (trend == exp_trend)
    if is_aligned:
        aligned_cnt += 1
    else:
        unaligned_list.append((r_id, sym, rdir, tf, t_bo, trend, exp_trend, swings, asof))

    align_str = "YES" if is_aligned else "NO (UNALIGNED)"
    swing_str = " -> ".join([f"{s[1]}@{s[2]:.4f} ({s[3].strftime('%m-%d')})" for s in swings]) if swings else "None"
    print(f"{r_id:<3} | {sym:<8} | {rdir:<10} | {pair:<7} | {t_bo.strftime('%Y-%m-%d %H:%M'):<17} | {trend:<12} | {align_str:<9} | {swing_str}")

print("-" * 120)
print(f"TOTAL ALIGNED: {aligned_cnt} / 35 ({aligned_cnt / 35 * 100:.1f}%)")
print(f"TOTAL UNALIGNED: {len(unaligned_list)} / 35 ({len(unaligned_list) / 35 * 100:.1f}%)")
print("=" * 120)

print("\n" + "=" * 80)
print("DETAILED EXPLANATION OF UNALIGNED SETUPS")
print("=" * 80)
for item in unaligned_list:
    r_id, sym, rdir, tf, t_bo, trend, exp_trend, swings, asof = item
    print(f"\nSETUP #{r_id}: {sym} {rdir} (Alert time: {t_bo})")
    print(f"  Expected Trend: {exp_trend} vs Evaluated Trend: {trend}")
    print(f"  As-of D1 Candle Closed: {asof}")
    print("  Active Swing Points at as-of time:")
    for s in swings:
        print(f"    - {s[1]} at {s[2]:.5f} on {s[3]}")
    df_tf = cache.load_candles(sym, tf)
    c_asof = df_tf.loc[asof, "close"] if asof in df_tf.index else None
    print(f"  Close of as-of candle: {c_asof}")
    if swings and len(swings) >= 2:
        last_swing = swings[-1]
        prev_swing = swings[-2]
        print(f"  Analysis: Last swing is {last_swing[1]}@{last_swing[2]:.5f}, previous is {prev_swing[1]}@{prev_swing[2]:.5f}.")
        if last_swing[1] == "TROUGH":
            print(f"    To flip BULLISH, close must exceed target peak {prev_swing[2]:.5f}. Close was {c_asof}.")
        else:
            print(f"    To flip BEARISH, close must fall below target trough {prev_swing[2]:.5f}. Close was {c_asof}.")
