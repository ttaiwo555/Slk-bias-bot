"""
fetch_deep_history.py
---------------------
Fetch deep history for all 37 active instruments (USOIL excluded):
- Daily: >= 18 months (since 2025-04-01)
- H4: >= 13 months (since 2025-09-01)
- Weekly: >= 24 months (since 2024-09-01)

Saves directly into slk_bot/storage/candles.db.
"""

import os
import sys
import time
from pathlib import Path
import pandas as pd
import yaml
from dotenv import load_dotenv

if sys.platform == "win32":
    os.system("chcp 65001 > nul")

load_dotenv()

from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import resample, apply_open_continuity, get_profile

ROOT = Path(__file__).resolve().parent
INST_PATH = ROOT / "slk_bot" / "config" / "instruments.yaml"
SETTINGS_PATH = ROOT / "slk_bot" / "config" / "settings.yaml"

with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

cache = CandleCache()
oanda = OandaProvider()
crypto = CryptoProvider()

SINCE_D = pd.Timestamp("2025-04-01", tz="UTC")
SINCE_H4 = pd.Timestamp("2025-09-01", tz="UTC")
SINCE_W = pd.Timestamp("2024-09-01", tz="UTC")

instruments = INST_DATA.get("instruments", [])

print(f"Total instruments to process: {len(instruments)}")

for idx, inst in enumerate(instruments, 1):
    name = inst["name"]
    asset_class = inst.get("asset_class", "forex")
    align = inst.get("alignment", "forex")
    oanda_sym = inst.get("oanda_symbol")
    crypto_sym = inst.get("binance_symbol") or inst.get("bybit_symbol")
    prof = get_profile(align)
    open_cont = prof.get("open_continuity", False)

    if inst.get("exclude_from_triggers") or name == "USOIL":
        print(f"[{idx}/{len(instruments)}] Skipping excluded instrument {name}")
        continue

    print(f"[{idx}/{len(instruments)}] Fetching {name} ({asset_class})...", flush=True)

    try:
        if asset_class == "crypto":
            # Crypto: fetch D, W, H4 from crypto provider
            df_d = crypto.get_candles(crypto_sym, "D", since=SINCE_D)
            df_w = crypto.get_candles(crypto_sym, "W", since=SINCE_W)
            df_h4 = crypto.get_candles(crypto_sym, "H4", since=SINCE_H4)
            cache.save_candles(name, "D", df_d)
            cache.save_candles(name, "W", df_w)
            cache.save_candles(name, "H4", df_h4)
            print(f"  -> {name}: D={len(df_d)}, W={len(df_w)}, H4={len(df_h4)}")

        elif asset_class == "commodity" and name in ("XAUUSD", "XAGUSD"):
            # Metals: Daily & Weekly from OANDA native, H4 resampled from H1 (18:00 NY anchor)
            df_d = oanda.get_candles(oanda_sym, "D", since=SINCE_D)
            df_w = oanda.get_candles(oanda_sym, "W", since=SINCE_W)
            if open_cont:
                df_d = apply_open_continuity(df_d)
                df_w = apply_open_continuity(df_w)
            cache.save_candles(name, "D", df_d)
            cache.save_candles(name, "W", df_w)

            # H4 resampled from H1
            h1 = oanda.get_candles(oanda_sym, "H1", since=SINCE_H4)
            df_h4 = resample(h1, "H4", "metal", h4_anchor="18:00", h4_timezone="America/New_York")
            cache.save_candles(name, "H4", df_h4)
            print(f"  -> {name}: D={len(df_d)}, W={len(df_w)}, H4={len(df_h4)} (resampled from {len(h1)} H1)")

        else:
            # Forex and Indices: Daily, Weekly, H4 native (17:00 NY anchor)
            df_d = oanda.get_candles(oanda_sym, "D", since=SINCE_D)
            df_w = oanda.get_candles(oanda_sym, "W", since=SINCE_W)
            df_h4 = oanda.get_candles(oanda_sym, "H4", since=SINCE_H4)

            if open_cont:
                df_d = apply_open_continuity(df_d)
                df_w = apply_open_continuity(df_w)
                df_h4 = apply_open_continuity(df_h4)

            cache.save_candles(name, "D", df_d)
            cache.save_candles(name, "W", df_w)
            cache.save_candles(name, "H4", df_h4)
            print(f"  -> {name}: D={len(df_d)}, W={len(df_w)}, H4={len(df_h4)}")

        time.sleep(0.1)

    except Exception as e:
        print(f"  ERROR fetching {name}: {e}", flush=True)

print("Done fetching deep history!")
