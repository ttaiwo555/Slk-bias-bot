import sys
sys.path.insert(0, '.')
from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity

cache = CandleCache()
df_eur = apply_open_continuity(cache.load_candles("EURUSD", "D"))
df_btc = apply_open_continuity(cache.load_candles("BTCUSD", "D"))
df_usoil_w = apply_open_continuity(cache.load_candles("USOIL", "W"))

print("EURUSD Daily tail:")
print(df_eur.tail(3).index)

print("BTCUSD Daily tail:")
print(df_btc.tail(3).index)

print("USOIL Weekly tail:")
print(df_usoil_w.tail(3).index)
