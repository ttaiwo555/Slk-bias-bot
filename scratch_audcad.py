import sqlite3
import pandas as pd
from slk_bot.strategy.levels import track_key_levels

conn = sqlite3.connect('slk_bot/storage/candles.db')
df_aud = pd.read_sql_query("SELECT * FROM candles WHERE symbol='AUDCAD' AND timeframe='W' ORDER BY open_time ASC", conn)
df_aud['open_time'] = pd.to_datetime(df_aud['open_time'])
df_aud.set_index('open_time', inplace=True)

# Find candle 2026-09-27
idx = df_aud.index.get_loc(pd.Timestamp('2026-09-27 21:00:00+00:00'))
c = df_aud.iloc[idx]
r = df_aud.iloc[idx-1]
print(f"c (2026-09-27): open={c.open}, high={c.high}, low={c.low}, close={c.close}")
print(f"r (2026-09-20): open={r.open}, high={r.high}, low={r.low}, close={r.close}")
print(f"r direction: close - open = {r.close - r.open}")

# Let's check levels before c
df_before = df_aud.iloc[:idx]
levels = track_key_levels(df_before, timeframe='W', symbol='AUDCAD')
for lvl in levels:
    if abs(lvl.price - 0.9853) < 0.005:
        print(f"Level near 0.9853: {lvl}")
