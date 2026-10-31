import sqlite3
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd

from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import resample
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels

conn = sqlite3.connect('slk_bot/storage/candles.db')
c = conn.cursor()
for sym in ['XAGUSD', 'XAUUSD']:
    c.execute("DELETE FROM candles WHERE symbol = ? AND timeframe = 'H4'", (sym,))
conn.commit()

cache = CandleCache()
oanda = OandaProvider()

for sym, oanda_sym in [('XAGUSD', 'XAG_USD'), ('XAUUSD', 'XAU_USD')]:
    h1 = oanda.get_candles(oanda_sym, 'H1', since=pd.Timestamp('2025-09-01', tz='UTC'))
    h4 = resample(h1, 'H4', 'metal') # ny17
    cache.save_candles(sym, 'H4', h4)
    print(f'Clean {sym} H4: {len(h4)} candles')

h4_clean = cache.load_candles('XAGUSD', 'H4')
print('\nClean XAGUSD H4 candles 12-15 Oct:')
for ts in h4_clean.loc['2026-10-12':'2026-10-15'].index:
    print(ts, 'open:', h4_clean.loc[ts, 'open'], 'high:', h4_clean.loc[ts, 'high'], 'low:', h4_clean.loc[ts, 'low'], 'close:', h4_clean.loc[ts, 'close'])

print('\nClean H4 levels for XAGUSD:')
levels = detect_ltf_levels(h4_clean, 'XAGUSD', 'H4', 0.0)
for k, lvl in levels:
    if '2026-10-1' in str(lvl.formation_time):
        print(lvl.formation_time, lvl.kind, f'{lvl.price:.5f}')
