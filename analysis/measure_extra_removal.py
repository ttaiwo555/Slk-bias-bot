import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
import yaml

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.models import TriggerDirection
from analysis.compare_trend_timing import get_trend_a
from analysis.parse_reference import parse_reference_file

cache = CandleCache()
ref_file = ROOT / "reference_alerts.txt"
ref_alerts = parse_reference_file(ref_file)
ignored = {"EU50", "JPN225", "DOTUSDT"}
ref_in_scope = [a for a in ref_alerts if a.symbol not in ignored]

with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
    inst_data = yaml.safe_load(f)

instruments = [
    inst for inst in inst_data.get("instruments", [])
    if not inst.get("exclude_from_triggers") and inst["name"] not in ignored
]

START_DATE = pd.Timestamp("2026-10-05 00:00:00", tz="UTC")
END_DATE = pd.Timestamp("2026-10-17 23:59:59", tz="UTC")

all_engine_alerts = []

print("Generating engine alerts...")
for inst in instruments:
    sym = inst["name"]
    asset_class = inst.get("asset_class", "forex")
    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    df_w = cache.load_candles(sym, "W")

    if not df_d.empty and not df_h4.empty:
        trigs_d = detect_triggers(df_d, sym, "D", asset_class, lookback=250, max_disrespects=3, ocl_max_disrespects=3)
        h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
        for t in trigs_d:
            if t.trigger_candle_time >= pd.Timestamp("2026-09-25", tz="UTC"):
                s = evaluate_setup_lifecycle(t, df_d, df_h4, df_d, sym, "D", "H4", True, all_ltf_levels=h4_levels)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_DATE <= a.breakout_candle_time <= END_DATE:
                        all_engine_alerts.append(a)

    if not df_w.empty and not df_d.empty:
        trigs_w = detect_triggers(df_w, sym, "W", asset_class, lookback=104, max_disrespects=3, ocl_max_disrespects=3)
        d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
        for t in trigs_w:
            if t.trigger_candle_time >= pd.Timestamp("2026-09-01", tz="UTC"):
                s = evaluate_setup_lifecycle(t, df_w, df_d, df_d, sym, "W", "D", True, all_ltf_levels=d_levels)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_DATE <= a.breakout_candle_time <= END_DATE:
                        all_engine_alerts.append(a)

print(f"Total engine external alerts (trend unrequired): {len(all_engine_alerts)}")

# Test trend filters on all engine alerts
aligned_rule_a = []
aligned_rule_c = []

for a in all_engine_alerts:
    t_a = get_trend_a(a.symbol, a.htf, a.breakout_candle_time)
    exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
    if t_a == exp:
        aligned_rule_a.append(a)

print(f"Engine external alerts with Rule (a) Trend Alignment REQUIRED: {len(aligned_rule_a)}")
removed_a = len(all_engine_alerts) - len(aligned_rule_a)
print(f"Rule (a) removed {removed_a} / {len(all_engine_alerts)} alerts ({removed_a / len(all_engine_alerts) * 100:.1f}% reduction)!")

# Filter specifically to 12-16 Oct (the exact week of reference_alerts.txt)
t_start = pd.Timestamp("2026-10-12 00:00:00", tz="UTC")
t_end = pd.Timestamp("2026-10-16 23:59:59", tz="UTC")
w_unreq = [a for a in all_engine_alerts if t_start <= a.breakout_candle_time <= t_end]
w_req = [a for a in aligned_rule_a if t_start <= a.breakout_candle_time <= t_end]

print(f"\nDuring 12-16 Oct (matching reference alert week):")
print(f"  External alerts (Trend Unrequired): {len(w_unreq)} ({len(w_unreq)/5:.1f} per weekday)")
print(f"  External alerts (Trend Required):   {len(w_req)} ({len(w_req)/5:.1f} per weekday)")
