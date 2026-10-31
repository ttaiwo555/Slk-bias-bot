import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.strategy.levels import detect_formation

cache = CandleCache()
df_h4 = cache.load_candles("EURCHF", "H4")

print("="*80)
print("EURCHF H4 CANDLES FROM 2026-10-09 09:00 TO 2026-10-12 17:00 UTC (CURRENT CACHE)")
print("="*80)
sub = df_h4[(df_h4.index >= "2026-10-09 09:00:00+00:00") & (df_h4.index <= "2026-10-12 17:00:00+00:00")]

for t, r in sub.iterrows():
    color = "BULLISH (green)" if r.close > r.open else ("BEARISH (red)" if r.close < r.open else "DOJI (neutral)")
    body = abs(r.close - r.open)
    body_pct = (body / r.close) * 100.0
    print(f"{t} | O={r.open:.5f} H={r.high:.5f} L={r.low:.5f} C={r.close:.5f} | {color:<15} | Body={body:.5f} ({body_pct:.4f}%)")

print("\nFormations detected in this window:")
for k in range(1, len(sub)):
    r_prev = sub.iloc[k-1]
    r_curr = sub.iloc[k]
    form = detect_formation(r_prev.open, r_prev.close, r_curr.open, r_curr.close, 0.0)
    if form:
        print(f"  Between {sub.index[k-1]} and {sub.index[k]}: Formation={form[0].value} at price={r_prev.close:.5f}")

# Now test OANDA price=B and price=A for EURCHF
provider = OandaProvider()
for ptype in ["M", "B", "A"]:
    provider.price_type = ptype
    print(f"\nFetching EURCHF H4 from OANDA with price={ptype}...")
    since_dt = pd.Timestamp("2026-10-08", tz="UTC")
    df_p = provider.get_candles("EUR_CHF", "H4", since=since_dt)
    sub_p = df_p[(df_p.index >= "2026-10-09 09:00:00+00:00") & (df_p.index <= "2026-10-12 17:00:00+00:00")]
    print(f"OANDA price={ptype} candles:")
    for t, r in sub_p.iterrows():
        color = "BULLISH" if r.close > r.open else ("BEARISH" if r.close < r.open else "DOJI")
        body = abs(r.close - r.open)
        print(f"  {t} | O={r.open:.5f} H={r.high:.5f} L={r.low:.5f} C={r.close:.5f} | {color:<7} | Body={body:.5f}")
    
    # Check V-shapes in this window
    print(f"V-shapes detected under price={ptype}:")
    for k in range(1, len(sub_p)):
        form = detect_formation(sub_p.iloc[k-1].open, sub_p.iloc[k-1].close, sub_p.iloc[k].open, sub_p.iloc[k].close, 0.0)
        if form and "v" in form[0].value.lower():
            print(f"  V-shape at {sub_p.index[k-1]}: price={sub_p.iloc[k-1].close:.5f}")
