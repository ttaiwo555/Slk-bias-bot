import sys
sys.path.insert(0, '.')
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.levels import track_key_levels

cache = CandleCache()
df = pd.read_csv('analysis/output/oct19_25_setups.csv')

print("Sample setups with 'Nov' in formation:")
nov_rows = df[df['formation_candle_1_wat'].str.contains('Nov') | df['formation_candle_2_wat'].str.contains('Nov')]
for idx, r in nov_rows.head(6).iterrows():
    sym = r['instrument']
    kl_price = float(r['key_level_price'])
    df_d = apply_open_continuity(cache.load_candles(sym, 'D'))
    lvls = track_key_levels(df_d, 'D', sym, lookback=None, max_disrespects=None)
    cands = [l for l in lvls if abs(l.price - kl_price) / kl_price < 0.001]
    if cands:
        cands.sort(key=lambda l: abs(l.price - kl_price))
        lvl = cands[0]
        print(f"{sym} {r['direction']} KL={kl_price} | Rej Candle (WAT)={r['htf_candle_open_wat']} | Formation Date={lvl.formation_time} (Year {lvl.formation_time.year})")
