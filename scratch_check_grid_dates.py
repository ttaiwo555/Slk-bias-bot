import pandas as pd
from dotenv import load_dotenv
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import resample

load_dotenv()
p = OandaProvider()

# Let's fetch H1 candles around Fri 16 Oct to Sun 18 Oct
# and test resampling for each of the requested instruments.

symbols = {
    "USOIL": "WTICO_USD",
    "NAS100": "NAS100_USD",
    "SPX500": "SPX500_USD",
    "US30": "US30_USD",
    "GER40": "DE30_EUR",
    "UK100": "UK100_GBP",
    "XAGUSD": "XAG_USD",
    "XAUUSD": "XAU_USD",
}

for name, sym in symbols.items():
    print(f"\n==================== {name} ({sym}) ====================")
    h1 = p.get_candles(sym, "H1", count=400)
    print("H1 time range:", h1.index.min(), "to", h1.index.max())

    if name == "XAGUSD":
        # Check Oct 09 around 14:00 and 18:00 UTC
        print("XAGUSD H1 around 2026-10-09:")
        sub = h1.loc["2026-10-09 12:00:00+00:00":"2026-10-09 22:00:00+00:00"]
        print(sub[['open', 'high', 'low', 'close']])
    elif name == "USOIL":
        # Check Oct 16 to Oct 19
        print("USOIL H1 around 2026-10-16 to 2026-10-19:")
        sub = h1.loc["2026-10-16 18:00:00+00:00":"2026-10-19 04:00:00+00:00"]
        print(sub[['open', 'high', 'low', 'close']])
    else:
        # Check Oct 16 17:00 UTC and Oct 18 21:00 UTC
        print(f"{name} H1 around 2026-10-16 to 2026-10-19:")
        sub = h1.loc["2026-10-16 16:00:00+00:00":"2026-10-19 02:00:00+00:00"]
        print(sub[['open', 'high', 'low', 'close']])
