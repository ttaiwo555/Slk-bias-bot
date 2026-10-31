import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import detect_formation
from slk_bot.strategy.setups import detect_ltf_levels, find_mitigation_candle, get_candle_close_time
from slk_bot.strategy.models import TriggerDirection, LevelKind

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

for rid in [3, 35]:
    ref = ref_df[ref_df["id"] == rid].iloc[0]
    sym = ref["symbol"]
    pair = ref["pair"]
    htf_tf = "W" if "W1" in pair else "D"
    ltf_tf = "D" if "W1" in pair else "H4"
    rdir = ref["dir"]
    ref_kl = float(ref["kl_price"])
    ref_bo_p = float(ref["bo_price"])
    ref_rej = pd.Timestamp(ref["rej_open_utc"], tz="UTC")
    ref_bo_t = pd.Timestamp(ref["bo_open_utc"], tz="UTC")
    
    print("="*80)
    print(f"SETUP #{rid}: {sym} {rdir} {pair}")
    print(f"Reference: Rej={ref_rej}, KL={ref_kl} ({ref['kl_kind']}), BO_Price={ref_bo_p}, BO_Time={ref_bo_t}")
    print("="*80)
    
    df_ltf = cache.load_candles(sym, ltf_tf)
    sub = df_ltf[(df_ltf.index >= ref_rej - pd.Timedelta(days=3)) & (df_ltf.index <= ref_bo_t + pd.Timedelta(days=1))]
    print(f"{ltf_tf} candles around setup:")
    for t, r in sub.iterrows():
        color = "BULLISH" if r.close > r.open else ("BEARISH" if r.close < r.open else "DOJI")
        body = abs(r.close - r.open)
        body_pct = (body / r.close) * 100.0
        print(f"  {t} | O={r.open:.2f} H={r.high:.2f} L={r.low:.2f} C={r.close:.2f} | {color:<7} | Body={body:.2f} ({body_pct:.3f}%)")
        
    # Check all LTF levels
    levels = detect_ltf_levels(df_ltf, sym, ltf_tf, 0.0)
    print(f"\nLTF levels formed in window:")
    for conf_idx, lvl in levels:
        if ref_rej - pd.Timedelta(days=5) <= lvl.formation_time <= ref_bo_t:
            print(f"  {lvl.kind.value:<8} @ {lvl.price:.2f} formed={lvl.formation_time} (conf={lvl.formation_confirmed_time})")
