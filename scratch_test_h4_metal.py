import pandas as pd
from dotenv import load_dotenv
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import resample, apply_open_continuity
from slk_bot.strategy.levels import track_key_levels

load_dotenv()
p = OandaProvider()

# Let's fetch XAG_USD H1
h1 = p.get_candles("XAG_USD", "H1", count=800)

# Resample H4 with 18:00 NY anchor
# In summer (EDT, UTC-4), 18:00 NY = 22:00 UTC.
# The 6 slots in NY time: 18:00, 22:00, 02:00, 06:00, 10:00, 14:00 NY
# In UTC: 22:00, 02:00, 06:00, 10:00, 14:00, 18:00 UTC!
# On Friday, market closes at 17:00 NY = 21:00 UTC.
# So the 14:00 UTC candle is 14:00-18:00 UTC.
# The 18:00 UTC candle is 18:00-21:00 UTC.

# Let's test custom H4 resampling for metals with 18:00 NY anchor
prof_metal = {
    "daily_open": "18:00",
    "tz": "America/New_York",
    "weekly_anchor_day": 6,
    "open_continuity": True,
}

from slk_bot.data.candles import _resample_h4
h4_metal = _resample_h4(h1, prof_metal)
h4_metal = apply_open_continuity(h4_metal)

print("H4 Metal around 2026-10-09:")
sub = h4_metal.loc["2026-10-09 10:00:00+00:00":"2026-10-12 00:00:00+00:00"]
print(sub[['open', 'high', 'low', 'close']])

# Track levels as of 2026-10-11 21:00 UTC
sub_asof = h4_metal.loc[:"2026-10-11 21:00:00+00:00"]
lvls = track_key_levels(sub_asof, "H4", "XAGUSD", lookback=300)
print("\nH4 levels near 60.811:")
for l in lvls:
    if abs(l.price - 60.811) / 60.811 < 0.002:
        print(f"  {l.kind.value} @ {l.price:.5f} | Role: {l.current_role.value} | Formed: {l.formation_time} | Unbroken: {l.is_unbroken}")
