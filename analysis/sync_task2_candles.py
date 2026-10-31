import sys
import time
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()

from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import resample, apply_open_continuity, get_profile

def main():
    cache = CandleCache()
    oanda = OandaProvider()
    crypto = CryptoProvider()

    SINCE_D = pd.Timestamp("2025-04-01", tz="UTC")
    SINCE_H4 = pd.Timestamp("2025-09-01", tz="UTC")
    SINCE_W = pd.Timestamp("2024-09-01", tz="UTC")

    # 1. USOIL
    print("Fetching USOIL...", flush=True)
    df_w_oil = oanda.get_candles("WTICO_USD", "W", since=SINCE_W)
    df_d_oil = oanda.get_candles("WTICO_USD", "D", since=SINCE_D)
    df_h4_oil = oanda.get_candles("WTICO_USD", "H4", since=SINCE_H4)
    df_w_oil = apply_open_continuity(df_w_oil)
    df_d_oil = apply_open_continuity(df_d_oil)
    df_h4_oil = apply_open_continuity(df_h4_oil)
    cache.save_candles("USOIL", "W", df_w_oil)
    cache.save_candles("USOIL", "D", df_d_oil)
    cache.save_candles("USOIL", "H4", df_h4_oil)
    print(f"USOIL saved: W={len(df_w_oil)}, D={len(df_d_oil)}, H4={len(df_h4_oil)}", flush=True)

    # 2. XAGUSD & XAUUSD: H4 on standard 17:00 NY grid
    print("\nFetching & Resampling Metals on ny17 grid...", flush=True)
    for sym, oanda_sym in [("XAGUSD", "XAG_USD"), ("XAUUSD", "XAU_USD")]:
        h1 = oanda.get_candles(oanda_sym, "H1", since=SINCE_H4)
        h4 = resample(h1, "H4", "metal")  # metal defaults to 17:00 NY
        cache.save_candles(sym, "H4", h4)
        print(f"{sym} H4 saved: {len(h4)} candles (ny17 grid)", flush=True)

    # 3. Crypto:
    print("\nFetching Crypto...", flush=True)
    for base_sym in ["BTC", "ETH"]:
        pair_usdt = f"{base_sym}USDT"
        print(f"Fetching {pair_usdt} H1...", flush=True)
        h1_crypto = crypto.get_candles(pair_usdt, "H1", since=SINCE_H4)
        print(f"Fetched {len(h1_crypto)} H1 candles for {pair_usdt}", flush=True)

        # A. <BASE>USD on crypto_ny17
        name_usd = f"{base_sym}USD"
        d_ny17 = resample(h1_crypto, "D", "crypto_ny17")
        h4_ny17 = resample(h1_crypto, "H4", "crypto_ny17")
        w_ny17 = resample(h1_crypto, "W", "crypto_ny17")
        cache.save_candles(name_usd, "W", w_ny17)
        cache.save_candles(name_usd, "D", d_ny17)
        cache.save_candles(name_usd, "H4", h4_ny17)
        print(f"{name_usd} (ny17) saved: W={len(w_ny17)}, D={len(d_ny17)}, H4={len(h4_ny17)}", flush=True)

        # B. <BASE>USDT on crypto (UTC)
        name_usdt = pair_usdt
        d_utc = resample(h1_crypto, "D", "crypto")
        h4_utc = resample(h1_crypto, "H4", "crypto")
        w_utc = resample(h1_crypto, "W", "crypto")
        cache.save_candles(name_usdt, "W", w_utc)
        cache.save_candles(name_usdt, "D", d_utc)
        cache.save_candles(name_usdt, "H4", h4_utc)
        print(f"{name_usdt} (UTC) saved: W={len(w_utc)}, D={len(d_utc)}, H4={len(h4_utc)}", flush=True)

    print("\nSync completed successfully!", flush=True)

if __name__ == "__main__":
    main()
