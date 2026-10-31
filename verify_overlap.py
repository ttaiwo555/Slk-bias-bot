import pandas as pd
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import _resample_daily, _resample_weekly, get_profile, apply_open_continuity

o = OandaProvider()

for sym, name, prof_name in [('EUR_USD', 'EURUSD', 'forex'), ('XAU_USD', 'XAUUSD', 'commodity'), ('NAS100_USD', 'NAS100', 'us_index')]:
    prof = get_profile(prof_name)
    h1 = o.get_candles(sym, 'H1', count=800)
    
    # Resample daily and weekly
    res_d = _resample_daily(h1, prof)
    res_w = _resample_weekly(h1, prof)
    
    # Drop the first (incomplete slice) candle from resampled
    res_d = res_d.iloc[1:]
    res_w = res_w.iloc[1:]
    
    nat_d = o.get_candles(sym, 'D', count=len(res_d) + 5)
    nat_w = o.get_candles(sym, 'W', count=len(res_w) + 5)
    
    # Compare Daily
    c_d = res_d.index.intersection(nat_d.index)
    d_diff = (res_d.loc[c_d, ['open', 'high', 'low', 'close']] - nat_d.loc[c_d, ['open', 'high', 'low', 'close']]).abs()
    
    # Compare Weekly
    c_w = res_w.index.intersection(nat_w.index)
    w_diff = (res_w.loc[c_w, ['open', 'high', 'low', 'close']] - nat_w.loc[c_w, ['open', 'high', 'low', 'close']]).abs()
    
    print(f"=== {name} ({sym}) ===")
    print(f"  Daily ({len(c_d)} candles): max abs diff = {d_diff.max().max():.6f}")
    if d_diff.max().max() > 0:
        print("    Daily max diff per col:", d_diff.max().to_dict())
    print(f"  Weekly ({len(c_w)} candles): max abs diff = {w_diff.max().max():.6f}")
    if w_diff.max().max() > 0:
        print("    Weekly max diff per col:", w_diff.max().to_dict())
