from slk_bot.data.providers.crypto import CryptoProvider
c = CryptoProvider()
df_d = c.get_candles("ETHUSDT", "D", count=350)
df_w = c.get_candles("ETHUSDT", "W", count=150)
print(f"Fetched {len(df_d)} D and {len(df_w)} W for ETHUSDT")
