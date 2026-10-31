"""
Quick diagnostic: print raw H1 candles from OANDA around the calibration dates
so we can see exactly what timestamps the data has.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import resample

oanda = OandaProvider()

# Fetch 2000 H1 bars
h1 = oanda.get_candles("EUR_USD", "H1", count=2000)
print(f"H1 bars: {len(h1)}, range {h1.index[0]} to {h1.index[-1]}")
print()

# Show H1 candles around the transition on Oct 13 21:00 UTC
mask = (h1.index >= "2026-10-13 19:00") & (h1.index <= "2026-10-14 02:00")
print("=== Raw H1 bars around Oct 13 21:00 UTC ===")
print(h1[mask][["open","high","low","close"]].to_string())
print()

# Show what the resampler produces for Oct 13 and Oct 14 daily bars
daily = resample(h1, "D", "forex")
print("=== Resampled Daily bars (Candidate A = forex) ===")
mask_d = (daily.index >= "2026-10-12 00:00") & (daily.index <= "2026-10-16 00:00")
print(daily[mask_d][["open","high","low","close"]].to_string())
print()
print("=== TradingView values for comparison ===")
print("Oct 13 daily  open=1.11539  high=1.12048  low=1.11524  close=1.11976")
print("Oct 14 daily  open=1.11976  high=1.12114  low=1.11130  close=1.11561")
print("Oct 15 daily  open=1.11561  high=1.12410  low=1.11497  close=1.12212")
print("Oct 16 daily  open=1.12213  high=1.12696  low=1.12079  close=1.12390")
