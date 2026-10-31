import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from analysis.evaluate_trend_three_moments import get_trend_at_time
from slk_bot.storage.cache import CandleCache

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

print("--- MISMATCHES FOR CANDIDATE (c) AT ALERT TIME ---")
unaligned = []
for idx, r in ref_df.iterrows():
    sym = r["symbol"]
    rdir = r["dir"]
    exp = "BULLISH" if rdir == "BUY" else "BEARISH"
    pair = r["pair"]
    tf = "W" if "W1" in pair else "D"
    t_bo = pd.Timestamp(r["bo_open_utc"], tz="UTC")
    df_tf = cache.load_candles(sym, tf)
    prior_d = df_tf[df_tf.index < t_bo]
    asof = prior_d.index[-1] if not prior_d.empty else t_bo
    trend, debug = get_trend_at_time(sym, tf, asof, "c")
    if trend != exp:
        unaligned.append((r["id"], sym, rdir, trend, asof))
        print(f"  #{r['id']:>2} {sym:>7} Ref={rdir} vs Trend={trend} (asof={asof})")

print(f"\nTotal unaligned: {len(unaligned)}")
