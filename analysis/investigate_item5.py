import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.levels import detect_formation
from slk_bot.strategy.setups import detect_ltf_levels, find_mitigation_candle, get_candle_close_time
from slk_bot.strategy.models import TriggerDirection, LevelKind

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

for rid in [12, 26, 28]:
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
    print(f"INVESTIGATING ITEM 5: SETUP #{rid} - {sym} {rdir} {pair}")
    print(f"Reference: Rej={ref_rej}, KL={ref_kl} ({ref['kl_kind']}), BO_Price={ref_bo_p}, BO_Time={ref_bo_t}")
    print("="*80)

    df_htf = cache.load_candles(sym, htf_tf)
    df_ltf = cache.load_candles(sym, ltf_tf)

    t_trig = ref_rej
    t_trig_close = get_candle_close_time(t_trig, htf_tf)

    sub_trig = df_ltf[(df_ltf.index >= t_trig) & (df_ltf.index < t_trig_close)]
    touches = sub_trig[(sub_trig["low"] <= ref_kl + 1e-5) & (sub_trig["high"] >= ref_kl - 1e-5)]
    
    first_touch_t = touches.index[0] if not touches.empty else None
    first_touch_idx = df_ltf.index.get_loc(first_touch_t) if first_touch_t is not None else None
    print(f"1. First-Touch Candle: {first_touch_t} (idx={first_touch_idx})")
    if first_touch_t is not None:
        r = df_ltf.loc[first_touch_t]
        print(f"   OHLC: O={r.open:.5f} H={r.high:.5f} L={r.low:.5f} C={r.close:.5f}")
    else:
        print("   No touch found inside trigger window! Checked lows and highs:")
        for t, r in sub_trig.iterrows():
            print(f"     {t}: L={r.low:.5f}, H={r.high:.5f} vs KL={ref_kl}")

    # Detect all LTF levels
    all_levels = detect_ltf_levels(df_ltf, sym, ltf_tf, 0.0)
    target_kind = LevelKind.V_SHAPE if exp_dir == TriggerDirection.BEARISH else LevelKind.A_SHAPE

    print(f"\n2. Candidate Turning Points (kind={target_kind.value}) formed before first-touch:")
    limit_idx = first_touch_idx if first_touch_idx is not None else len(df_ltf)
    cands = [lvl for conf_idx, lvl in all_levels if lvl.kind == target_kind and lvl.formation_index < limit_idx]
    
    # Sort by formation time
    cands.sort(key=lambda l: l.formation_index)
    print(f"   Total found: {len(cands)}. Recent candidates (last 8):")
    for lvl in cands[-8:]:
        # Check intactness up to first touch
        c_conf = lvl.formation_index + 1
        closes = df_ltf["close"].values[c_conf : limit_idx]
        if exp_dir == TriggerDirection.BEARISH:
            intact = not (closes < lvl.price).any()
            viol = [c for c in closes if c < lvl.price]
        else:
            intact = not (closes > lvl.price).any()
            viol = [c for c in closes if c > lvl.price]
        diff_from_ref_bo = abs(lvl.price - ref_bo_p) / ref_bo_p * 100.0
        print(f"     form_idx={lvl.formation_index} time={lvl.formation_time} price={lvl.price:.5f} | intact={intact} (viols={len(viol)}) | diff from ref BO ({ref_bo_p:.5f}) = {diff_from_ref_bo:.3f}%")

    print(f"\n3. Reference Breakout Level: {ref_bo_p}")
    # Search all LTF levels for ref_bo_p
    matching_levels = [lvl for conf_idx, lvl in all_levels if abs(lvl.price - ref_bo_p) / ref_bo_p <= 0.002]
    if matching_levels:
        print(f"   Found {len(matching_levels)} levels matching reference breakout price {ref_bo_p}:")
        for lvl in matching_levels:
            print(f"     {lvl.kind.value} @ {lvl.price:.5f}, formed={lvl.formation_time}, conf={lvl.formation_confirmed_time}")
            # Why was it not selected or broken?
            if lvl.formation_index >= limit_idx:
                print(f"       -> Not selected because formed AFTER or ON first touch (form_idx {lvl.formation_index} >= {limit_idx})")
            else:
                c_conf = lvl.formation_index + 1
                closes = df_ltf["close"].values[c_conf : limit_idx]
                if exp_dir == TriggerDirection.BEARISH and (closes < lvl.price).any():
                    print(f"       -> Not selected because NOT INTACT before first touch (closed below before touch)")
                elif exp_dir == TriggerDirection.BULLISH and (closes > lvl.price).any():
                    print(f"       -> Not selected because NOT INTACT before first touch (closed above before touch)")
                else:
                    print(f"       -> Intact! Was there a more recent intact level?")
    else:
        print(f"   NO level found in entire LTF history matching reference breakout price {ref_bo_p} within 0.2%!")
        # Let's inspect LTF candles around ref_bo_t to see what price was doing at ref_bo_t
        print(f"\n   LTF candles around ref_bo_time ({ref_bo_t}):")
        sub_bo = df_ltf[(df_ltf.index >= ref_bo_t - pd.Timedelta(hours=12)) & (df_ltf.index <= ref_bo_t + pd.Timedelta(hours=12))]
        for t, r in sub_bo.iterrows():
            print(f"     {t}: O={r.open:.5f} H={r.high:.5f} L={r.low:.5f} C={r.close:.5f}")
