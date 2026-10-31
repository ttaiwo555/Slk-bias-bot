import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.setups import detect_ltf_levels, get_candle_close_time
from slk_bot.strategy.models import TriggerDirection, LevelKind

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

for rid in [12, 26]:
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
    exp_dir = TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH

    print("="*80)
    print(f"SETUP #{rid}: {sym} {rdir} {pair}")
    print(f"Reference: Rej={ref_rej}, KL={ref_kl}, BO_Price={ref_bo_p}, BO_Time={ref_bo_t}")
    print("="*80)

    df_ltf = cache.load_candles(sym, ltf_tf)
    t_trig = ref_rej
    t_trig_close = get_candle_close_time(t_trig, htf_tf)
    sub_trig = df_ltf[(df_ltf.index >= t_trig) & (df_ltf.index < t_trig_close)]
    touches = sub_trig[(sub_trig["low"] <= ref_kl + 1e-5) & (sub_trig["high"] >= ref_kl - 1e-5)]
    first_touch_t = touches.index[0] if not touches.empty else None
    first_touch_idx = df_ltf.index.get_loc(first_touch_t) if first_touch_t is not None else None
    print(f"First touch candle: {first_touch_t} (idx={first_touch_idx})")
    if first_touch_t is not None:
        r = df_ltf.loc[first_touch_t]
        print(f"  OHLC: O={r.open:.5f} H={r.high:.5f} L={r.low:.5f} C={r.close:.5f}")

    all_levels = detect_ltf_levels(df_ltf, sym, ltf_tf, 0.0)
    target_kind = LevelKind.V_SHAPE if exp_dir == TriggerDirection.BEARISH else LevelKind.A_SHAPE
    limit_idx = first_touch_idx if first_touch_idx is not None else len(df_ltf)
    cands = [lvl for conf_idx, lvl in all_levels if lvl.kind == target_kind and lvl.formation_index < limit_idx]
    cands.sort(key=lambda l: l.formation_index)
    print(f"Candidate turning points (last 6):")
    for lvl in cands[-6:]:
        c_conf = lvl.formation_index + 1
        closes = df_ltf["close"].values[c_conf : limit_idx]
        if exp_dir == TriggerDirection.BEARISH:
            intact = not (closes < lvl.price).any()
        else:
            intact = not (closes > lvl.price).any()
        print(f"  idx={lvl.formation_index} time={lvl.formation_time} price={lvl.price:.5f} intact={intact}")
    
    print(f"Reference BO price: {ref_bo_p}")
    match_ref = [lvl for conf_idx, lvl in all_levels if abs(lvl.price - ref_bo_p) / ref_bo_p <= 0.001]
    print(f"Matching levels to ref BO price within 0.1%: {len(match_ref)}")
    for lvl in match_ref:
        print(f"  {lvl.kind.value} @ {lvl.price:.5f} formed={lvl.formation_time} idx={lvl.formation_index} (limit_idx={limit_idx})")
        c_conf = lvl.formation_index + 1
        closes = df_ltf["close"].values[c_conf : limit_idx]
        if exp_dir == TriggerDirection.BEARISH:
            intact = not (closes < lvl.price).any()
            viol = [c for c in closes if c < lvl.price]
        else:
            intact = not (closes > lvl.price).any()
            viol = [c for c in closes if c > lvl.price]
        print(f"    intact before touch? {intact} (viols={len(viol)})")
        
    # Check why breakout was not reached
    print("\nForward check of candles after trigger:")
    sub_fwd = df_ltf[(df_ltf.index >= t_trig) & (df_ltf.index <= ref_bo_t + pd.Timedelta(days=2))]
    for t, r in sub_fwd.iterrows():
        print(f"  {t}: O={r.open:.5f} H={r.high:.5f} L={r.low:.5f} C={r.close:.5f}")
