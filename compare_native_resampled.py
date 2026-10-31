import sys
import pandas as pd
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import (
    _resample_daily,
    _resample_weekly,
    get_profile,
    apply_open_continuity,
)

o = OandaProvider()

print("=" * 80, flush=True)
print(" COMPARISON: OANDA NATIVE D/W CANDLES vs RESAMPLED H1 CANDLES", flush=True)
print(" Settings: alignmentTimezone='America/New_York', dailyAlignment=17, weeklyAlignment='Sunday'", flush=True)
print("=" * 80, flush=True)

instruments = [
    ("EUR_USD", "EURUSD", "forex"),
    ("XAU_USD", "XAUUSD", "commodity"),
    ("NAS100_USD", "NAS100", "us_index"),
]

for sym, name, prof_name in instruments:
    print(f"\nFetching {name} ({sym})...", flush=True)
    prof = get_profile(prof_name)
    h1 = o.get_candles(sym, "H1", count=1500)

    # 1. Daily comparison
    res_d_raw = _resample_daily(h1, prof)
    nat_d_raw = o.get_candles(sym, "D", count=len(res_d_raw) + 5)
    common_d = res_d_raw.index.intersection(nat_d_raw.index)

    sub_res_d = res_d_raw.loc[common_d]
    sub_nat_d = nat_d_raw.loc[common_d]
    diff_d_raw = (sub_res_d[['open', 'high', 'low', 'close']] - sub_nat_d[['open', 'high', 'low', 'close']]).abs()
    max_d_raw = diff_d_raw.max().max()

    # Normalized Daily
    res_d_norm = apply_open_continuity(res_d_raw)
    nat_d_norm = apply_open_continuity(nat_d_raw)
    diff_d_norm = (res_d_norm.loc[common_d, ['open', 'high', 'low', 'close']] - nat_d_norm.loc[common_d, ['open', 'high', 'low', 'close']]).abs()
    max_d_norm = diff_d_norm.max().max()

    print(f"  • Daily: Overlap = {len(common_d)} candles", flush=True)
    print(f"      Raw Match (resampled vs native):        max abs diff = {max_d_raw:.6f}", flush=True)
    print(f"      Normalized Match (with continuity):      max abs diff = {max_d_norm:.6f}", flush=True)

    # 2. Weekly comparison
    res_w_raw = _resample_weekly(h1, prof)
    nat_w_raw = o.get_candles(sym, "W", count=len(res_w_raw) + 5)
    common_w = res_w_raw.index.intersection(nat_w_raw.index)

    sub_res_w = res_w_raw.loc[common_w]
    sub_nat_w = nat_w_raw.loc[common_w]
    diff_w_raw = (sub_res_w[['open', 'high', 'low', 'close']] - sub_nat_w[['open', 'high', 'low', 'close']]).abs()
    max_w_raw = diff_w_raw.max().max()

    res_w_norm = apply_open_continuity(res_w_raw)
    nat_w_norm = apply_open_continuity(nat_w_raw)
    diff_w_norm = (res_w_norm.loc[common_w, ['open', 'high', 'low', 'close']] - nat_w_norm.loc[common_w, ['open', 'high', 'low', 'close']]).abs()
    max_w_norm = diff_w_norm.max().max()

    print(f"  • Weekly: Overlap = {len(common_w)} candles", flush=True)
    print(f"      Raw Match (resampled vs native):        max abs diff = {max_w_raw:.6f}", flush=True)
    print(f"      Normalized Match (with continuity):      max abs diff = {max_w_norm:.6f}", flush=True)
