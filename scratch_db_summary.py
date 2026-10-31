import sqlite3
import pandas as pd

conn = sqlite3.connect('slk_bot/storage/candles.db')
df = pd.read_sql_query("SELECT symbol, timeframe, COUNT(*) as cnt, MIN(open_time) as min_time, MAX(open_time) as max_time FROM candles GROUP BY symbol, timeframe ORDER BY symbol, timeframe", conn)
print(df.to_string())
