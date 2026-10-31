import pandas as pd
from dotenv import load_dotenv
from slk_bot.data.providers.oanda import OandaProvider

load_dotenv()
p = OandaProvider()
h1 = p.get_candles("WTICO_USD", "H1", count=200)

# Let's test assigning H4 slots for USOIL with UTC anchor
# 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
# Notice that Sunday 22:00 UTC is after 20:00.
# If Sunday 22:00 starts its own bar at 22:00 UTC:
# Let's test a custom slotting function for USOIL:

def usoil_h4_open(ts: pd.Timestamp) -> pd.Timestamp:
    # If Sunday 22:00 or 23:00:
    # In Sunday (dayofweek == 6), trading opens at 22:00 UTC.
    if ts.dayofweek == 6 and ts.hour >= 22:
        return ts.floor("D") + pd.Timedelta(hours=22)
    # Standard 4-hour slots: 0, 4, 8, 12, 16, 20
    slot_hour = (ts.hour // 4) * 4
    return ts.floor("D") + pd.Timedelta(hours=slot_hour)

df = h1.copy()
df["bar_open_utc"] = [usoil_h4_open(ts) for ts in df.index]

from slk_bot.data.candles import _aggregate, apply_open_continuity
h4 = _aggregate(df, "bar_open_utc", "H4", "commodity")
h4 = apply_open_continuity(h4)

sub = h4.loc["2026-10-16 16:00:00+00:00":"2026-10-19 08:00:00+00:00"]
print("USOIL Resampled H4 around Oct 16-19:")
print(sub[['open', 'high', 'low', 'close']])
