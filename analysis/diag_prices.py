import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
import yaml

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from analysis.parse_reference import parse_reference_file

cache = CandleCache()
ref_alerts = parse_reference_file(ROOT / "reference_alerts.txt")

for ref_idx in [5, 9, 10, 11, 13, 40]:  # 0-indexed: 5=AUDNZD, 9=EURCAD, 10=AUDUSD, 11=EURJPY, 13=AUDJPY, 40=ETHUSDT
    ref = ref_alerts[ref_idx]
    sym = ref.symbol
    print(f"\nRef #{ref_idx+1}: {sym} {ref.direction} {ref.pair} Rej:{ref.rejection_candle_open_utc} KL:{ref.level_price} BO:{ref.breakout_price} BO_time:{ref.breakout_candle_open_utc}")

    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    trigs = detect_triggers(df_d, sym, "D", "forex" if "USD" in sym or "JPY" in sym or "NZD" in sym or "CAD" in sym else "crypto")
    h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)

    for t in trigs:
        if t.trigger_candle_time == ref.rejection_candle_open_utc:
            s = evaluate_setup_lifecycle(t, df_d, df_h4, df_d, sym, "D", "H4", True, all_ltf_levels=h4_levels)
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL:
                    print(f"   Engine Alert: Rej:{t.trigger_candle_time} KL:{(a.htf_level.price if a.htf_level else 'None')} BO_price:{a.broken_level.price} BO_time:{a.breakout_candle_time}")
                    print(f"      Diff KL: {abs(a.htf_level.price - ref.level_price) if a.htf_level else 'N/A'}")
                    print(f"      Diff BO: {abs(a.broken_level.price - ref.breakout_price)}")
                    print(f"      Diff BO %: {abs(a.broken_level.price - ref.breakout_price) / ref.breakout_price * 100:.4f}%")
