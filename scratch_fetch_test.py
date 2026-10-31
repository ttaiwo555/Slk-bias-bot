import time
from slk_bot.data.providers.oanda import OandaProvider
from dotenv import load_dotenv

load_dotenv()
p = OandaProvider()
t0 = time.time()
d_candles = p.get_candles("GBP_USD", "D", count=350)
w_candles = p.get_candles("GBP_USD", "W", count=150)
print(f"Fetched {len(d_candles)} D candles and {len(w_candles)} W candles in {time.time()-t0:.2f}s")
