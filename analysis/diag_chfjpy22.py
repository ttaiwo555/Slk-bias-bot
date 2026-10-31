import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import (
    evaluate_sweep,
    evaluate_rejection_multi,
    detect_triggers,
)
from slk_bot.strategy.levels import (
    detect_formation,
    apply_candle_to_level,
)
from slk_bot.strategy.models import TriggerDirection, LevelKind, Side, KeyLevel

cache = CandleCache()
df_d = cache.load_candles("CHFJPY", "D")

# Let's run candle-by-candle simulation leading up to 2026-10-12 21:00
opens = df_d["open"].values
highs = df_d["high"].values
lows = df_d["low"].values
closes = df_d["close"].values
times = df_d.index

levels = []
t_target = pd.Timestamp("2026-10-12 21:00:00", tz="UTC")
target_k = None

for k in range(1, len(df_d)):
    if times[k] == t_target:
        target_k = k
        break

print(f"Target candle k={target_k}, time={times[target_k]}")
print(f"O={opens[target_k]} H={highs[target_k]} L={lows[target_k]} C={closes[target_k]}")
print(f"Prior candle k-1 time={times[target_k-1]}: O={opens[target_k-1]} H={highs[target_k-1]} L={lows[target_k-1]} C={closes[target_k-1]}")

# Build levels up to target_k
for k in range(1, target_k + 1):
    t_k = times[k]
    o_k, h_k, l_k, c_k = opens[k], highs[k], lows[k], closes[k]
    o_prev, c_prev = opens[k-1], closes[k-1]
    
    if k == target_k:
        # Before applying candle target_k, check active levels!
        print(f"\nTotal active levels before candle {t_target}: {len(levels)}")
        # Look for levels near 190.399
        print("\nLevels near 190.399 (within 0.1):")
        for lvl in levels:
            if abs(lvl.price - 190.399) < 0.1:
                print(f"  {lvl.kind.value} @ {lvl.price:.5f}, form_time={lvl.formation_time}, role={lvl.current_role.value}, last_side={lvl.last_side.value}, disrep={lvl.disrespect_count}, fresh={lvl.fresh}")
                
        # Check sweep evaluation
        sweep_dir, sweep_ref, s_wick_m, s_close_m = evaluate_sweep(
            candle_time=t_k,
            candle_open=o_k,
            candle_high=h_k,
            candle_low=l_k,
            candle_close=c_k,
            ref_time=times[k-1],
            ref_open=opens[k-1],
            ref_high=highs[k-1],
            ref_low=lows[k-1],
            ref_close=closes[k-1],
        )
        print(f"\nSweep evaluation on {t_target}:")
        print(f"  sweep_dir={sweep_dir}, sweep_ref={sweep_ref}, s_wick_m={s_wick_m}, s_close_m={s_close_m}")
        
        # Check rejections evaluation
        res_bull, res_bear = evaluate_rejection_multi(
            candle_low=l_k,
            candle_high=h_k,
            candle_close=c_k,
            active_levels=levels,
            timeframe="D",
            primary_selection_rule="fresh_first",
        )
        print(f"\nBullish rejection evaluation:")
        if res_bull:
            prim, all_rej, r_wick_m, r_close_m = res_bull
            print(f"  Primary: {prim.kind.value} @ {prim.price:.5f}, all count={len(all_rej)}")
        else:
            print("  None")
            
        print(f"\nBearish rejection evaluation:")
        if res_bear:
            prim, all_rej, r_wick_m, r_close_m = res_bear
            print(f"  Primary: {prim.kind.value} @ {prim.price:.5f}, all count={len(all_rej)}")
            for lvl in all_rej:
                print(f"    all_rej: {lvl.kind.value} @ {lvl.price:.5f}")
        else:
            print("  None! Why was res_bear None?")
            # Let's inspect sell-eligible levels!
            from slk_bot.strategy.triggers import is_sell_role
            sell_eligible = [l for l in levels if is_sell_role(l)]
            print(f"  Total sell-eligible levels: {len(sell_eligible)}")
            for l in sell_eligible:
                if abs(l.price - 190.399) < 0.1:
                    print(f"    Sell eligible near 190.399: {l.kind.value} @ {l.price:.5f}, role={l.current_role}, last_side={l.last_side}")
                    # Check rejection conditions on candle
                    # For bearish: candle_high >= l.price and candle_close <= l.price
                    cond1 = h_k >= l.price
                    cond2 = c_k <= l.price
                    print(f"    High >= level? {cond1} (H={h_k} vs {l.price}), Close <= level? {cond2} (C={c_k} vs {l.price})")
            
        # Check conflict resolution between bull and bear:
        skip_bull_m1 = False
        skip_bear_m1 = False
        if res_bull is not None and res_bear is not None and sweep_dir is None:
            pen_bull = max(0.0, res_bull[0].price - l_k)
            pen_bear = max(0.0, h_k - res_bear[0].price)
            if pen_bull < 1e-8 and pen_bear > 1e-8:
                skip_bull_m1 = True
            elif pen_bear < 1e-8 and pen_bull > 1e-8:
                skip_bear_m1 = True
        print(f"\nConflict checks: skip_bull_m1={skip_bull_m1}, skip_bear_m1={skip_bear_m1}")
        break

    # Normal step: detect formation and update levels
    formation = detect_formation(o_prev, c_prev, o_k, c_k, 0.0)
    if formation is not None:
        kind, role, side = formation
        lvl_price = float(c_prev)
        lvl = KeyLevel(
            id=f"CHFJPY_D_{kind.value}_{times[k-1].strftime('%Y%m%d%H%M')}_{lvl_price:.5f}",
            symbol="CHFJPY",
            timeframe="D",
            kind=kind,
            price=lvl_price,
            formation_time=times[k-1],
            formation_confirmed_time=t_k,
            formation_index=k-1,
            current_role=role,
            disrespect_count=0,
            last_side=side,
            fresh=True,
        )
        levels.append(lvl)
    
    # Apply candle k to all levels
    for lvl in levels:
        apply_candle_to_level(lvl, c_k, h_k, l_k, max_disrespects=None, ocl_max_disrespects=None)
