import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
import yaml

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from analysis.parse_reference import parse_reference_file

cache = CandleCache()
ref_alerts = parse_reference_file(ROOT / "reference_alerts.txt")

# Check the 6 missing reference alerts
missing_indices = [2, 15, 26, 31, 33, 37] # 0-indexed in all_ref_alerts
for idx in missing_indices:
    ref = ref_alerts[idx]
    sym = ref.symbol
    print(f"\n=======================================================")
    print(f"Missing Ref #{idx+1}: {sym} {ref.direction} {ref.pair} Rej:{ref.rejection_candle_open_utc} KL:{ref.level_price} ({ref.level_kind}) BO:{ref.breakout_price} BO_time:{ref.breakout_candle_open_utc}")
    print(f"=======================================================")

    df_d = cache.load_candles(sym, "D")
    if df_d.empty:
        print(f"NO DAILY CANDLES FOR {sym}!")
        continue

    # Print daily candle at ref_rej
    if ref.rejection_candle_open_utc in df_d.index:
        c = df_d.loc[ref.rejection_candle_open_utc]
        print(f"Rejection candle {ref.rejection_candle_open_utc}: open={c['open']}, high={c['high']}, low={c['low']}, close={c['close']}")
    else:
        print(f"Candle {ref.rejection_candle_open_utc} NOT in {sym} daily index! Nearest indices:")
        sub = df_d.loc[ref.rejection_candle_open_utc - pd.Timedelta(days=2) : ref.rejection_candle_open_utc + pd.Timedelta(days=2)]
        for ts in sub.index:
            print(f"  {ts}")

    # Check triggers
    asset_class = "crypto" if "BTC" in sym or "ETH" in sym else ("index" if "500" in sym or "40" in sym or "30" in sym else "forex")
    trigs = detect_triggers(df_d, sym, "D", asset_class, lookback=250, max_disrespects=3, ocl_max_disrespects=3)
    matching_trigs = [t for t in trigs if t.trigger_candle_time == ref.rejection_candle_open_utc]
    print(f"Triggers at {ref.rejection_candle_open_utc}: {len(matching_trigs)}")
    for t in matching_trigs:
        print(f"  Trig: {t.direction} {t.method} extreme_X={t.mitigation_extreme} primary_lvl={(t.primary_level.price if t.primary_level else 'None')} kind={(t.primary_level.kind if t.primary_level else 'None')}")
        if t.all_rejected_levels:
            print(f"  All rejected levels: {[(l.price, l.kind.value, l.current_role.value) for l in t.all_rejected_levels]}")
