import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
import yaml

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.models import TriggerDirection, TriggerMethod
from analysis.compare_trend_timing import get_trend_a

cache = CandleCache()
with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
    inst_data = yaml.safe_load(f)

ignored = {"EU50", "JPN225", "DOTUSDT"}
instruments = [
    inst for inst in inst_data.get("instruments", [])
    if not inst.get("exclude_from_triggers") and inst["name"] not in ignored
]

t_start = pd.Timestamp("2026-10-12 00:00:00", tz="UTC")
t_end = pd.Timestamp("2026-10-16 23:59:59", tz="UTC")

pushed_unreq = []
pushed_req = []

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
                # Method 1 unfresh excluded from push
                if t.method == TriggerMethod.METHOD_1 and not (t.primary_level and t.primary_level.fresh):
                    continue
                s = evaluate_setup_lifecycle(t, df_d, df_h4, df_d, sym, "D", "H4", unfresh_alerts=False, all_ltf_levels=h4_levels)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and t_start <= a.breakout_candle_time <= t_end:
                        pushed_unreq.append(a)
                        # Check trend
                        t_trend = get_trend_a(sym, "D", a.breakout_candle_time)
                        exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                        if t_trend == exp:
                            pushed_req.append(a)

    if not df_w.empty and not df_d.empty:
        trigs_w = detect_triggers(df_w, sym, "W", asset_class, lookback=104, max_disrespects=3, ocl_max_disrespects=3)
        d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
        for t in trigs_w:
            if t.trigger_candle_time >= pd.Timestamp("2026-09-01", tz="UTC"):
                if t.method == TriggerMethod.METHOD_1 and not (t.primary_level and t.primary_level.fresh):
                    continue
                s = evaluate_setup_lifecycle(t, df_w, df_d, df_d, sym, "W", "D", unfresh_alerts=False, all_ltf_levels=d_levels)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and t_start <= a.breakout_candle_time <= t_end:
                        pushed_unreq.append(a)
                        t_trend = get_trend_a(sym, "W", a.breakout_candle_time)
                        exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                        if t_trend == exp:
                            pushed_req.append(a)

# Deduplicate to unique (symbol, direction, date)
def dedupe_daily(alerts):
    seen = set()
    out = []
    for a in alerts:
        key = (a.symbol, a.direction, a.breakout_candle_time.date())
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out

deduped_unreq = dedupe_daily(pushed_unreq)
deduped_req = dedupe_daily(pushed_req)

print(f"\n=======================================================")
print(f"OCTOBER 12-16 (5 Weekdays) Under Push Policy:")
print(f"=======================================================")
print(f"Total Pushed Alerts (Trend Unrequired): {len(pushed_unreq)} ({len(pushed_unreq)/5:.1f} / weekday)")
print(f"Unique Setups (Trend Unrequired):       {len(deduped_unreq)} ({len(deduped_unreq)/5:.1f} / weekday)")
print(f"\nTotal Pushed Alerts (Trend Required):   {len(pushed_req)} ({len(pushed_req)/5:.1f} / weekday)")
print(f"Unique Setups (Trend Required):         {len(deduped_req)} ({len(deduped_req)/5:.1f} / weekday)")
print(f"Reference Alerts (Unique Setups):       35 (7.0 / weekday)")
