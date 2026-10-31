import sqlite3
import pandas as pd
conn = sqlite3.connect('slk_bot/storage/candles.db')
cursor = conn.cursor()
cursor.execute("SELECT DISTINCT timeframe FROM candles WHERE symbol='AUDCAD'")
print("Timeframes:", cursor.fetchall())
df_w = pd.read_sql_query("SELECT * FROM candles WHERE symbol='AUDCAD' AND timeframe='W' ORDER BY open_time ASC", conn)
print(df_w.tail(6)[['open_time', 'open', 'high', 'low', 'close']])
