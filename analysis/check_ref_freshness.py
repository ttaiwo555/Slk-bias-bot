import sys
sys.path.insert(0, '.')
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.levels import track_key_levels

cache = CandleCache()
ref = pd.read_csv('reference_setups.csv')

unfresh_count = 0
fresh_count = 0
not_found = 0

for _, r in ref.iterrows():
    sym = r['symbol']
    tf = 'W' if r['pair'] == 'W1-D1' else 'D'
    kl_price = float(r['kl_price'])
    rej_utc = pd.Timestamp(r['rej_open_utc'], tz='UTC')
    
    df = apply_open_continuity(cache.load_candles(sym, tf))
    pre_rej = df[df.index < rej_utc]
    lvls = track_key_levels(pre_rej, tf, sym, lookback=None, max_disrespects=None)
    
    matches = [l for l in lvls if abs(l.price - kl_price) / kl_price < 0.001]
    if matches:
        matches.sort(key=lambda l: abs(l.price - kl_price))
        m = matches[0]
        if m.fresh:
            fresh_count += 1
        else:
            unfresh_count += 1
            print(f"Unfresh match found: {sym} {r['dir']} KL={kl_price} fresh={m.fresh} last_contact={m.last_contact} disrespects={m.disrespect_count}")
    else:
        not_found += 1
        print(f"No exact level match for {sym} {r['dir']} KL={kl_price}")

print(f"\nFinal Summary for 35 Reference Setups:")
print(f"Fresh: {fresh_count}")
print(f"Unfresh: {unfresh_count}")
print(f"Not found: {not_found}")
