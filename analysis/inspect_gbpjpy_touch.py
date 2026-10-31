import sys
sys.path.insert(0, '.')
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.levels import track_key_levels

cache = CandleCache()

# Let's inspect GBPJPY #16: KL = 208.893 (V-shape)
df = apply_open_continuity(cache.load_candles("GBPJPY", "D"))
rej_utc = pd.Timestamp("2026-10-12 21:00:00", tz="UTC")
pre_rej = df[df.index < rej_utc]
lvls = track_key_levels(pre_rej, "D", "GBPJPY", lookback=None, max_disrespects=None)
matches = [l for l in lvls if abs(l.price - 208.893) / 208.893 < 0.001]
for m in matches:
    print(f"GBPJPY match: formed={m.formation_time} price={m.price} kind={m.kind} fresh={m.fresh} last_contact={m.last_contact} disp={m.disrespect_count}")

# Check candles between formation and rejection
if matches:
    m = matches[0]
    between = df[(df.index > m.formation_confirmed_time) & (df.index < rej_utc)]
    print(f"Candles between formation ({m.formation_confirmed_time}) and rejection ({rej_utc}): {len(between)}")
    for t, c in between.iterrows():
        # Did it touch 208.893? For V-shape support, did low <= 208.893?
        if c['low'] <= m.price:
            print(f"  Touch at {t}: low={c['low']}, close={c['close']}, high={c['high']}")
