import sys
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.levels import track_key_levels
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.models import TriggerDirection, TriggerMethod

ref_df = pd.read_csv(ROOT / "reference_setups.csv")
print(f"Total reference setups: {len(ref_df)}")

# Load review_sheet or inspect each reference setup's key level
# In reference_setups.csv, we have columns:
# id, symbol, dir, tf, kl_kind, kl_price, bo_price, rej_open_utc, bo_open_utc, confirmed, notes
print(ref_df.head())
