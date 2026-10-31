"""
analysis/sync_oct19_25_candles.py
---------------------------------
Fetches recent candles through Oct 25, 2026 for all active instruments
and updates slk_bot/storage/candles.db.
"""
import os
import sys
import yaml
import time
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import resample

cache = CandleCache()
oanda = OandaProvider()
crypto = CryptoProvider()

with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
    idata = yaml.safe_load(f)

instruments = [inst for inst in idata["instruments"] if not inst.get("exclude_from_triggers")]
print(f"Updating candles for {len(instruments)} instruments through Oct 25...", flush=True)

for idx, inst in enumerate(instruments, 1):
    name = inst["name"]
    ac = inst.get("asset_class", "forex")
    oanda_sym = inst.get("oanda_symbol")
    crypto_sym = inst.get("binance_symbol") or inst.get("bybit_symbol")

    print(f"[{idx:02d}/{len(instruments)}] Fetching {name} ({ac})...", end="", flush=True)

    try:
        if ac == "crypto":
            df_d = crypto.get_candles(crypto_sym, "D", count=30)
            df_h4 = crypto.get_candles(crypto_sym, "H4", count=60)
            df_w = crypto.get_candles(crypto_sym, "W", count=10)
            cache.save_candles(name, "D", df_d)
            cache.save_candles(name, "H4", df_h4)
            cache.save_candles(name, "W", df_w)
            print(f" OK (D: {len(df_d)}, H4: {len(df_h4)})", flush=True)
        elif ac == "commodity" and name in ("XAUUSD", "XAGUSD"):
            df_d = oanda.get_candles(oanda_sym, "D", count=30)
            df_w = oanda.get_candles(oanda_sym, "W", count=10)
            h1 = oanda.get_candles(oanda_sym, "H1", count=120)
            df_h4 = resample(h1, "H4", "metal", h4_anchor="18:00", h4_timezone="America/New_York")
            cache.save_candles(name, "D", df_d)
            cache.save_candles(name, "W", df_w)
            cache.save_candles(name, "H4", df_h4)
            print(f" OK (D: {len(df_d)}, H4: {len(df_h4)})", flush=True)
        elif name == "USOIL":
            df_d = oanda.get_candles("WTICO_USD", "D", count=30)
            df_w = oanda.get_candles("WTICO_USD", "W", count=10)
            df_h4 = oanda.get_candles("WTICO_USD", "H4", count=60)
            cache.save_candles("USOIL", "D", df_d)
            cache.save_candles("USOIL", "W", df_w)
            cache.save_candles("USOIL", "H4", df_h4)
            print(f" OK (D: {len(df_d)}, H4: {len(df_h4)})", flush=True)
        else:
            df_d = oanda.get_candles(oanda_sym, "D", count=30)
            df_w = oanda.get_candles(oanda_sym, "W", count=10)
            df_h4 = oanda.get_candles(oanda_sym, "H4", count=60)
            cache.save_candles(name, "D", df_d)
            cache.save_candles(name, "W", df_w)
            cache.save_candles(name, "H4", df_h4)
            print(f" OK (D: {len(df_d)}, H4: {len(df_h4)})", flush=True)
        time.sleep(0.05)
    except Exception as e:
        print(f" ERROR: {e}", flush=True)

print("All instruments updated successfully!", flush=True)
