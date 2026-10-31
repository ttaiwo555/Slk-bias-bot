"""
analysis/inspect_raw_diagnoses_item5.py
---------------------------------------
Inspect raw candles and levels for:
1. #12 NZDJPY: BUY, D1->H4, ref KL V 88.62, ref BO 88.87 @ 2026-10-13T13:00
2. #26 AUDJPY: SELL, D1->H4, ref KL A 110.58, ref BO 110.046 @ 2026-10-14T21:00
3. #28 GER40: SELL, D1->H4, ref KL V 25295.0, ref BO 25252.6 @ 2026-10-14T21:00
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    find_mitigation_candle,
    get_candle_close_time,
)
from slk_bot.strategy.models import TriggerDirection

cache = CandleCache()

print("=" * 80)
print("DIAGNOSIS 1: #12 NZDJPY (BUY, D1->H4)")
print("=" * 80)
df_d = cache.load_candles("NZDJPY", "D")
df_h4 = cache.load_candles("NZDJPY", "H4")
trigs_d = detect_triggers(df_d, "D", "NZDJPY", "forex", lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first")
# Rejection is 2026-10-11 21:00 UTC (D1 candle)
t12 = [t for t in trigs_d if t.trigger_candle_time == pd.Timestamp("2026-10-11 21:00", tz="UTC") and t.direction == TriggerDirection.BULLISH]
print(f"Trigger count on 2026-10-11 21:00 for NZDJPY BUY: {len(t12)}")
if t12:
    t = t12[0]
    print(f"  Primary Level: {t.primary_level.kind.value} @ {t.primary_level.price:.5f}")
    print(f"  Trigger Candle: O={t.trigger_candle_open}, H={t.trigger_candle_high}, L={t.trigger_candle_low}, C={t.trigger_candle_close}")
    print(f"  Mitigation Extreme X = {t.mitigation_extreme}")
    print("  All rejected levels:")
    for l in t.all_rejected_levels:
        print(f"    - {l.kind.value} @ {l.price:.5f} formed {l.formation_time}")

    h4_levels = detect_ltf_levels(df_h4, "NZDJPY", "H4", 0.0)
    s = evaluate_setup_lifecycle(t, df_d, df_h4, df_d, "NZDJPY", "D", "H4", unfresh_alerts=True, all_ltf_levels=h4_levels, external_ref_point="first_touch", enable_invalidation=True)
    print(f"  Setup State: {s.state}")
    print(f"  LX Level: {s.timeline.lx_level.kind.value if s.timeline.lx_level else None} @ {s.timeline.lx_level.price if s.timeline.lx_level else None}")
    print(f"  Alerts produced: {len(s.alerts)}")
    for a in s.alerts:
        print(f"    - {a.breakout_type.value} @ {a.broken_level.price} at {a.breakout_candle_time}")

    # Check H4 candles during trigger window and subsequent walk
    sub_h4 = df_h4.loc["2026-10-11 21:00":"2026-10-13 18:00"]
    print("\n  Raw H4 Candles (2026-10-11 21:00 to 2026-10-13 18:00):")
    for t_idx, row in sub_h4.iterrows():
        print(f"    {t_idx}: O={row['open']:.3f}, H={row['high']:.3f}, L={row['low']:.3f}, C={row['close']:.3f}")

print("\n" + "=" * 80)
print("DIAGNOSIS 2: #26 AUDJPY (SELL, D1->H4)")
print("=" * 80)
df_d_aud = cache.load_candles("AUDJPY", "D")
df_h4_aud = cache.load_candles("AUDJPY", "H4")
trigs_aud = detect_triggers(df_d_aud, "D", "AUDJPY", "forex", lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first")
t26 = [t for t in trigs_aud if t.trigger_candle_time == pd.Timestamp("2026-10-12 21:00", tz="UTC") and t.direction == TriggerDirection.BEARISH]
if t26:
    t = t26[0]
    print(f"  Primary Level: {t.primary_level.kind.value} @ {t.primary_level.price:.5f}")
    print(f"  Trigger Candle: O={t.trigger_candle_open}, H={t.trigger_candle_high}, L={t.trigger_candle_low}, C={t.trigger_candle_close}")
    print(f"  Mitigation Extreme X = {t.mitigation_extreme}")

    h4_levels_aud = detect_ltf_levels(df_h4_aud, "AUDJPY", "H4", 0.0)
    s = evaluate_setup_lifecycle(t, df_d_aud, df_h4_aud, df_d_aud, "AUDJPY", "D", "H4", unfresh_alerts=True, all_ltf_levels=h4_levels_aud, external_ref_point="first_touch", enable_invalidation=True)
    print(f"  Setup State: {s.state}")
    print(f"  Invalidation Time: {s.timeline.invalidation_time}")
    print(f"  LX Level: {s.timeline.lx_level.kind.value if s.timeline.lx_level else None} @ {s.timeline.lx_level.price if s.timeline.lx_level else None}")

    sub_h4_aud = df_h4_aud.loc["2026-10-12 21:00":"2026-10-15 00:00"]
    print("\n  Raw H4 Candles around invalidation and breakout:")
    for t_idx, row in sub_h4_aud.iterrows():
        print(f"    {t_idx}: O={row['open']:.3f}, H={row['high']:.3f}, L={row['low']:.3f}, C={row['close']:.3f}")

print("\n" + "=" * 80)
print("DIAGNOSIS 3: #28 GER40 (SELL, D1->H4)")
print("=" * 80)
df_d_ger = cache.load_candles("GER40", "D")
df_h4_ger = cache.load_candles("GER40", "H4")
trigs_ger = detect_triggers(df_d_ger, "D", "GER40", "index", lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first")
t28 = [t for t in trigs_ger if t.trigger_candle_time == pd.Timestamp("2026-10-13 21:00", tz="UTC") and t.direction == TriggerDirection.BEARISH]
if t28:
    t = t28[0]
    print(f"  Primary Level: {t.primary_level.kind.value} @ {t.primary_level.price:.5f}")
    print(f"  Mitigation Extreme X = {t.mitigation_extreme}")
    print("  All rejected levels:")
    for l in t.all_rejected_levels:
        print(f"    - {l.kind.value} @ {l.price:.5f} formed {l.formation_time}")

    h4_levels_ger = detect_ltf_levels(df_h4_ger, "GER40", "H4", 0.0)
    s = evaluate_setup_lifecycle(t, df_d_ger, df_h4_ger, df_d_ger, "GER40", "D", "H4", unfresh_alerts=True, all_ltf_levels=h4_levels_ger, external_ref_point="first_touch", enable_invalidation=True)
    print(f"  Setup State: {s.state}")
    print(f"  LX Level: {s.timeline.lx_level.kind.value if s.timeline.lx_level else None} @ {s.timeline.lx_level.price if s.timeline.lx_level else None}")
    for a in s.alerts:
        print(f"    - {a.breakout_type.value} @ {a.broken_level.price} at {a.breakout_candle_time}")

    # Inspect all H4 levels near 25252.6 and 25295
    print("\n  H4 V-shape candidates formed before trigger/touch:")
    for conf_idx, lvl in h4_levels_ger:
        if lvl.kind.value == "V" and 25200 <= lvl.price <= 25450:
            form_time = df_h4_ger.index[lvl.formation_index]
            print(f"    - V @ {lvl.price:.1f} formed at {form_time} (conf_idx={conf_idx})")
