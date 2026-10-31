"""
analysis/fetch_parity_deep_history.py
-------------------------------------
Fetch deep history for parity run:
  - Weekly: 10 years (since 2016-10-01)
  - Daily: 5 years (since 2021-10-01)
For all instruments in reference_setups.csv (including USOIL and crypto).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import time
import pandas as pd
import yaml
from dotenv import load_dotenv

load_dotenv()

from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import apply_open_continuity, get_profile

cache = CandleCache()
oanda = OandaProvider()
crypto = CryptoProvider()

ref_df = pd.read_csv(ROOT / "reference_setups.csv")
in_scope_syms = set(ref_df["symbol"].unique())

with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
    inst_data = yaml.safe_load(f)

SINCE_D = pd.Timestamp("2021-10-01", tz="UTC")
SINCE_W = pd.Timestamp("2016-10-01", tz="UTC")

for inst in inst_data.get("instruments", []):
    sym = inst["name"]
    if sym not in in_scope_syms:
        continue
        
    ac = inst.get("asset_class", "forex")
    o_sym = inst.get("oanda_symbol")
    c_sym = inst.get("binance_symbol") or inst.get("bybit_symbol")
    align = inst.get("alignment", "forex")
    prof = get_profile(align)
    open_cont = prof.get("open_continuity", False)
    
    print(f"Fetching deep history for {sym} ({ac})...", flush=True)
    try:
        if ac == "crypto":
            df_d = crypto.get_candles(c_sym, "D", since=SINCE_D)
            df_w = crypto.get_candles(c_sym, "W", since=SINCE_W)
            cache.save_candles(sym, "D", df_d)
            cache.save_candles(sym, "W", df_w)
            print(f"  -> {sym}: D={len(df_d)}, W={len(df_w)}")
        else:
            df_d = oanda.get_candles(o_sym, "D", since=SINCE_D)
            df_w = oanda.get_candles(o_sym, "W", since=SINCE_W)
            if open_cont:
                df_d = apply_open_continuity(df_d)
                df_w = apply_open_continuity(df_w)
            cache.save_candles(sym, "D", df_d)
            cache.save_candles(sym, "W", df_w)
            print(f"  -> {sym}: D={len(df_d)}, W={len(df_w)}")
        time.sleep(0.1)
    except Exception as e:
        print(f"  ERROR {sym}: {e}", flush=True)

print("Done fetching parity deep history!")
