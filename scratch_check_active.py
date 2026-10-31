from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
import pandas as pd

cache = CandleCache()
df = cache.load_candles("AUDCAD", "W")
idx = df.index.get_loc("2026-09-27 21:00:00+00:00")
c = df.iloc[idx]

from slk_bot.strategy.levels import track_key_levels
levels_before = track_key_levels(df.iloc[:idx], "W", "AUDCAD", lookback=104, max_disrespects=3)

print("Active levels before candle k:")
for l in levels_before:
    if not l.is_retired:
        print(f"  {l.kind.value} @ {l.price:.5f} | Role: {l.current_role.value} | Disrespect: {l.disrespect_count} | Side: {l.last_side.value}")
