from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import track_key_levels
import pandas as pd

cache = CandleCache()
df_w = cache.load_candles("AUDCAD", "W")

# Let's inspect candles from 2026-09-01 onwards
print("AUDCAD Weekly candles:")
print(df_w.loc["2026-09-01":])

idx = df_w.index.get_loc("2026-09-27 21:00:00+00:00")
c = df_w.iloc[idx]
r = df_w.iloc[idx-1]
print("\ncandle c:", c.to_dict())
print("candle r:", r.to_dict())

# Check rejection
levels = track_key_levels(df_w.iloc[:idx], "W", "AUDCAD", lookback=104)
for l in levels:
    if abs(l.price - 0.9853) < 0.001:
        print("Matching level:", l)
        print(f"c.low ({c.low}) < l.price ({l.price})? {c.low < l.price}")
        print(f"c.close ({c.close}) > l.price ({l.price})? {c.close > l.price}")
        print(f"last_side: {l.last_side}")

# Check sweep conditions
print("\nSweep check:")
print(f"R is bearish? {r.close < r.open} (open={r.open}, close={r.close})")
print(f"c.low < r.low? {c.low < r.low} (c.low={c.low}, r.low={r.low})")
print(f"c.close > r.low? {c.close > r.low} (c.close={c.close}, r.low={r.low})")
print(f"c.high <= r.high? {c.high <= r.high} (c.high={c.high}, r.high={r.high})")
