import pandas as pd
from dotenv import load_dotenv
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import resample

load_dotenv()
p = OandaProvider()

print("Testing OANDA symbols and quotes around Oct 16-18, 2026...")

symbols = {
    "USOIL": "WTICO_USD",
    "NAS100": "NAS100_USD",
    "SPX500": "SPX500_USD",
    "US30": "US30_USD",
    "GER40": "DE30_EUR",
    "UK100": "UK100_GBP",
    "XAGUSD": "XAG_USD",
}

for name, sym in symbols.items():
    try:
        h1 = p.get_candles(sym, "H1", count=100)
        print(f"\n{name} ({sym}) latest H1 candles:")
        print(h1.tail(6)[['open', 'high', 'low', 'close']])
    except Exception as e:
        print(f"\n{name} ({sym}) error: {e}")
