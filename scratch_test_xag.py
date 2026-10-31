from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import track_key_levels
import pandas as pd

cache = CandleCache()
df_d = cache.load_candles("XAGUSD", "D")

idx = df_d.index.get_loc("2026-10-11 21:00:00+00:00")
c = df_d.iloc[idx]
r = df_d.iloc[idx-1]
print("XAGUSD c (2026-10-11):", c.to_dict())
print("XAGUSD r (2026-10-08):", r.to_dict())

# Levels before c
levels = track_key_levels(df_d.iloc[:idx], "D", "XAGUSD", lookback=250)
for l in levels:
    if abs(l.price - 61.35) < 0.1:
        print("\nMatching level:", l)
        print(f"c.high ({c.high}) > l.price ({l.price})? {c.high > l.price}")
        print(f"c.close ({c.close}) < l.price ({l.price})? {c.close < l.price}")
        print(f"last_side: {l.last_side}")

print("\nSweep check:")
print(f"R is bullish? {r.close > r.open} (open={r.open}, close={r.close})")
print(f"c.high > r.high? {c.high > r.high} (c.high={c.high}, r.high={r.high})")
print(f"c.close < r.high? {c.close < r.high} (c.close={c.close}, r.high={r.high})")
print(f"c.low >= r.low? {c.low >= r.low} (c.low={c.low}, r.low={r.low})")
