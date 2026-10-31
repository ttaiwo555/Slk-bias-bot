import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import detect_ltf_levels, find_mitigation_candle, get_candle_close_time
from slk_bot.strategy.models import TriggerDirection, LevelKind

cache = CandleCache()
df_d = cache.load_candles('SPX500', 'D')
df_h4 = cache.load_candles('SPX500', 'H4')

# Trigger rejection date: 2026-10-14 21:00 UTC
t_trig = pd.Timestamp('2026-10-14 21:00:00', tz='UTC')
t_trig_close = get_candle_close_time(t_trig, 'D')

print(f"Trigger window: {t_trig} to {t_trig_close}")
sub = df_h4[(df_h4.index >= t_trig) & (df_h4.index < t_trig_close)]
print("H4 candles in trigger window:")
for idx, (t, r) in enumerate(sub.iterrows()):
    print(f"  [{idx}] {t}: O={r.open:.1f} H={r.high:.1f} L={r.low:.1f} C={r.close:.1f}")

# Key level is 7679.8
kl_p = 7679.8
touches = sub[(sub['low'] <= kl_p + 1e-6) & (sub['high'] >= kl_p - 1e-6)]
print("Touches:")
for t, r in touches.iterrows():
    print(f"  Touch: {t}, L={r.low:.1f}, H={r.high:.1f}, C={r.close:.1f}")

ext_idx, ext_time = find_mitigation_candle(df_h4, t_trig, t_trig_close, TriggerDirection.BULLISH, sub['low'].min())
print(f"Extreme candle: {ext_time} (idx {ext_idx}) min low = {sub['low'].min()}")

touch_loc = df_h4.index.get_loc(touches.index[0])
print(f"First touch candle: {touches.index[0]} (idx {touch_loc})")

h4_levels = detect_ltf_levels(df_h4, 'SPX500', 'H4', 0.0)
print("\nA-shape levels formed before first touch:")
for conf_idx, lvl in h4_levels:
    if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index < touch_loc:
        print(f"  form_idx={lvl.formation_index} conf_idx={conf_idx} time={lvl.formation_time} price={lvl.price}")
