import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.levels import detect_formation
from slk_bot.strategy.setups import detect_ltf_levels, find_mitigation_candle, get_candle_close_time
from slk_bot.strategy.models import TriggerDirection, LevelKind

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

print("="*80)
print("INVESTIGATING ITEM 3: #5 AUDNZD and #22 CHFJPY")
print("="*80)

# --- #5 AUDNZD ---
print("\n--- #5 AUDNZD ---")
df_d_audnzd = cache.load_candles("AUDNZD", "D")
ref5 = ref_df[ref_df["id"] == 5].iloc[0]
print(f"Ref #5: {ref5.to_dict()}")

# Test AUDNZD under default settings:
trigs_def = detect_triggers(df_d_audnzd, "D", "AUDNZD", "forex")
trigs_def_5 = [t for t in trigs_def if t.trigger_candle_time == pd.Timestamp(ref5["rej_open_utc"], tz="UTC")]
print(f"Default triggers at {ref5['rej_open_utc']}: {len(trigs_def_5)}")
for t in trigs_def_5:
    print(f"  Dir={t.direction.value}, Method={t.method.value}, Prim={t.primary_level.kind.value if t.primary_level else None} @ {t.primary_level.price if t.primary_level else None}, fresh={t.primary_level.fresh if t.primary_level else None}")

# Now test each parity setting one by one:
# 1) lookback=None vs lookback=365
# 2) max_disrespects=None vs 3
# 3) primary_selection_rule='fresh_first' vs 'nearest_to_close'
settings_to_test = [
    ("Default (365, max_d=3, nearest_to_close)", dict(lookback=365, max_disrespects=3, ocl_max_disrespects=3, primary_selection_rule="nearest_to_close")),
    ("Setting: lookback=None only", dict(lookback=None, max_disrespects=3, ocl_max_disrespects=3, primary_selection_rule="nearest_to_close")),
    ("Setting: max_disrespects=None only", dict(lookback=365, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="nearest_to_close")),
    ("Setting: primary_selection_rule='fresh_first' only", dict(lookback=365, max_disrespects=3, ocl_max_disrespects=3, primary_selection_rule="fresh_first")),
    ("Parity combined (lookback=None, max_d=None, fresh_first)", dict(lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first")),
]

for name, kwargs in settings_to_test:
    trigs = detect_triggers(df_d_audnzd, "D", "AUDNZD", "forex", **kwargs)
    trigs_5 = [t for t in trigs if t.trigger_candle_time == pd.Timestamp(ref5["rej_open_utc"], tz="UTC")]
    print(f"\nUnder {name}:")
    print(f"  Count = {len(trigs_5)}")
    for t in trigs_5:
        p = t.primary_level
        print(f"    Dir={t.direction.value}, Method={t.method.value}, Prim={p.kind.value if p else None} @ {p.price if p else None}, fresh={p.fresh if p else None}")
        if t.all_rejected_levels:
            print(f"    All rejected levels ({len(t.all_rejected_levels)}):")
            for lvl in t.all_rejected_levels:
                print(f"      {lvl.kind.value} @ {lvl.price:.5f}, form_time={lvl.formation_time}, disrespects={lvl.disrespect_count}, fresh={lvl.fresh}")

# --- #22 CHFJPY ---
print("\n--- #22 CHFJPY ---")
df_d_chfjpy = cache.load_candles("CHFJPY", "D")
ref22 = ref_df[ref_df["id"] == 22].iloc[0]
print(f"Ref #22: {ref22.to_dict()}")

# Candle 2026-10-12 21:00 UTC
t_cand_22 = pd.Timestamp("2026-10-12 21:00:00", tz="UTC")
if t_cand_22 in df_d_chfjpy.index:
    c_row = df_d_chfjpy.loc[t_cand_22]
    print(f"Candle at {t_cand_22}: O={c_row.open}, H={c_row.high}, L={c_row.low}, C={c_row.close}")
else:
    print(f"Candle {t_cand_22} not directly found in index! Nearby candles:")
    print(df_d_chfjpy.loc["2026-10-10":"2026-10-15"])

# Detect triggers for CHFJPY under both default and parity
trigs_chf_def = detect_triggers(df_d_chfjpy, "D", "CHFJPY", "forex")
trigs_chf_par = detect_triggers(df_d_chfjpy, "D", "CHFJPY", "forex", lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first")

print(f"CHFJPY triggers (default) on or around 2026-10-12:")
for t in trigs_chf_def:
    if pd.Timestamp("2026-10-10", tz="UTC") <= t.trigger_candle_time <= pd.Timestamp("2026-10-15", tz="UTC"):
        print(f"  {t.trigger_candle_time} Dir={t.direction.value} Method={t.method.value} Prim={t.primary_level.kind.value if t.primary_level else None} @ {t.primary_level.price if t.primary_level else None}")

print(f"CHFJPY triggers (parity) on or around 2026-10-12:")
for t in trigs_chf_par:
    if pd.Timestamp("2026-10-10", tz="UTC") <= t.trigger_candle_time <= pd.Timestamp("2026-10-15", tz="UTC"):
        print(f"  {t.trigger_candle_time} Dir={t.direction.value} Method={t.method.value} Prim={t.primary_level.kind.value if t.primary_level else None} @ {t.primary_level.price if t.primary_level else None}")

# Check what levels exist around 190.399 at that time
print("\nChecking all active levels for CHFJPY before 2026-10-12 21:00 candle:")
# Let's inspect levels formed before 2026-10-12
