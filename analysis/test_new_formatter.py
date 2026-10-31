import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.bot.formatter import format_candle_display, format_kl_formation

# Test date formatting functions
t_thu_21 = pd.Timestamp("2026-10-15 21:00:00", tz="UTC")
print("Thu 21:00 UTC Daily:", format_candle_display(t_thu_21, "D"))

t_sun_w = pd.Timestamp("2026-10-11 21:00:00", tz="UTC")
print("Sun 21:00 UTC Weekly:", format_candle_display(t_sun_w, "W"))

t_h4 = pd.Timestamp("2026-10-12 05:00:00", tz="UTC")
print("Mon 05:00 UTC H4:", format_candle_display(t_h4, "H4"))
