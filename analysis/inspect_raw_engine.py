"""
analysis/inspect_raw_engine.py
------------------------------
Extract raw engine output for reference setups:
#1 EURGBP, #3 BTCUSD, #8 AUDUSD, #15 USOIL, #17 XAGUSD
"""
import sys
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    find_mitigation_candle,
    get_candle_close_time,
    BreakoutType,
)
from slk_bot.strategy.models import TriggerDirection, LevelKind

cache = CandleCache()

targets = [
    (1, "EURGBP", "SELL", "D", "H4", "2026-10-07T21:00", 0.84822, 0.84574, "2026-10-12T05:00"),
    (3, "BTCUSD", "SELL", "D", "H4", "2026-10-10T21:00", 84012.32, 82881.23, "2026-10-12T09:00"),
    (8, "AUDUSD", "SELL", "D", "H4", "2026-10-11T21:00", 0.69838, 0.69726, "2026-10-12T05:00"),
    (15, "USOIL", "BUY", "W", "D", "2026-10-04T21:00", 89.831, 95.388, "2026-10-12T21:00"),
    (17, "XAGUSD", "SELL", "D", "H4", "2026-10-11T21:00", 61.3495, 58.8805, "2026-10-14T05:00"),
]

for ref_id, sym, rdir, htf_tf, ltf_tf, rej_t, ref_kl, ref_bo, ref_bo_t in targets:
    print("=" * 80)
    print(f"SETUP #{ref_id}: {sym} {rdir} ({htf_tf}->{ltf_tf}) Rej:{rej_t}")
    print(f"REFERENCE: KL={ref_kl}, BO={ref_bo} @ {ref_bo_t}")
    print("-" * 80)
    
    df_htf = cache.load_candles(sym, htf_tf)
    df_ltf = cache.load_candles(sym, ltf_tf)
    
    # Run trigger detection
    lookback = 104 if htf_tf == "W" else 250
    ac = "crypto" if "BTC" in sym else ("commodities" if sym in ("USOIL", "XAGUSD") else "forex")
    trigs = detect_triggers(df_htf, htf_tf, sym, ac, lookback=lookback, max_disrespects=3, ocl_max_disrespects=3)
    
    t_target = pd.Timestamp(rej_t, tz="UTC")
    matching_trigs = [t for t in trigs if t.trigger_candle_time == t_target and (t.direction == TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH)]
    
    if not matching_trigs:
        print(f"NO TRIGGER FOUND for {sym} at {t_target}!")
        continue
        
    for idx_trig, trig in enumerate(matching_trigs):
        print(f"Trigger {idx_trig+1}: Method={trig.method}, Direction={trig.direction.value}")
        print(f"Primary Level: {trig.primary_level.kind.value if trig.primary_level else 'None'} @ {trig.primary_level.price if trig.primary_level else 'None'}")
        print("All Rejected Levels:")
        for lvl in trig.all_rejected_levels:
            print(f"  - Kind: {lvl.kind.value}, Price: {lvl.price:.5f}, Formed: {lvl.formation_time}, Fresh: {lvl.fresh}, Disrespects: {lvl.disrespect_count}, Role: {lvl.current_role.value}")
            
        # Detect LTF levels & run lifecycle
        ltf_levels = detect_ltf_levels(df_ltf, sym, ltf_tf, 0.0)
        setup = evaluate_setup_lifecycle(
            trigger=trig,
            htf_df=df_htf,
            ltf_df=df_ltf,
            daily_df=df_htf if htf_tf == "D" else cache.load_candles(sym, "D"),
            symbol=sym,
            htf_tf=htf_tf,
            ltf_tf=ltf_tf,
            unfresh_alerts=True,
            all_ltf_levels=ltf_levels,
        )
        
        print("\nTimeline & Breakout Candidates:")
        print(f"  Mitigation Extreme X: {setup.timeline.extreme_price_x} at {setup.timeline.extreme_candle_time}")
        print(f"  Chosen External Level (LX): {setup.timeline.lx_level.kind.value if setup.timeline.lx_level else 'None'} @ {setup.timeline.lx_level.price if setup.timeline.lx_level else 'None'}")
        
        # List candidate LTF turning points before extreme
        ext_idx = setup.timeline.extreme_candle_index
        if trig.direction == TriggerDirection.BEARISH:
            cands = [lvl for c_idx, lvl in ltf_levels if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index <= ext_idx and lvl.price < setup.timeline.extreme_price_x]
        else:
            cands = [lvl for c_idx, lvl in ltf_levels if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index <= ext_idx and lvl.price > setup.timeline.extreme_price_x]
        print(f"  Candidate LX turning points (total {len(cands)}):")
        for c in cands[-5:]:
            print(f"    * {c.kind.value} @ {c.price:.5f}, formed {c.formation_time} (idx {c.formation_index})")
            
        print("\nAlerts generated:")
        for a in setup.alerts:
            print(f"  - {a.breakout_type.value} BO @ {a.broken_level.price:.5f} at candle {a.breakout_candle_time} (Alert: {a.alert_time})")
    print()
