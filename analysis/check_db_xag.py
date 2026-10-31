import sqlite3

conn = sqlite3.connect('slk_bot/storage/candles.db')
c = conn.cursor()
c.execute("SELECT count(*) FROM candles WHERE symbol = 'XAGUSD' AND timeframe = 'H4'")
print('Total XAGUSD H4 in db:', c.fetchone()[0])
c.execute("SELECT open_time FROM candles WHERE symbol = 'XAGUSD' AND timeframe = 'H4' AND open_time LIKE '2026-10-13%' ORDER BY open_time")
for r in c.fetchall():
    print(r[0])
