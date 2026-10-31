import sqlite3
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.replay import load_instrument_profile, load_settings_thresholds

conn = sqlite3.connect('slk_bot/storage/candles.db')
cached_symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM candles").fetchall()]
print(f"Cached symbols in DB ({len(cached_symbols)}): {cached_symbols}")
