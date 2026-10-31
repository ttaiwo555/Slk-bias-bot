import pandas as pd
from dotenv import load_dotenv
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import resample
from slk_bot.strategy.levels import track_key_levels

load_dotenv()
p = OandaProvider()
h1 = p.get_candles("XAG_USD", "H1", count=1000)

h4 = resample(h1, "H4", "metal")
h4_asof = h4.loc[:"2026-10-11 21:00:00+00:00"]
levels = track_key_levels(h4_asof, "H4", "XAGUSD", lookback=300)

print("=" * 80)
print(" SILVER H4 GOLDEN CHECK (Target near 60.811 as of 2026-10-11 21:00 UTC)")
print("=" * 80)

found = [l for l in levels if abs(l.price - 60.811) / 60.811 <= 0.0015 and l.kind.value == "V"]
for l in found:
    diff_pct = abs(l.price - 60.811) / 60.811 * 100
    print(f"Kind: {l.kind.value:<8} | Price: {l.price:.5f} | Role: {l.current_role.value:<10} | Disrespect: {l.disrespect_count} | Unbroken: {l.is_unbroken} | Formed: {l.formation_time} | Diff: {diff_pct:.4f}%")

c14 = h4.loc["2026-10-09 14:00:00+00:00"]
c18 = h4.loc["2026-10-09 18:00:00+00:00"]
print("\nCandles forming the V-shape:")
print(f"  14:00 candle: O={c14['open']:.5f}, H={c14['high']:.5f}, L={c14['low']:.5f}, C={c14['close']:.5f} (Bearish)")
print(f"  18:00 candle: O={c18['open']:.5f}, H={c18['high']:.5f}, L={c18['low']:.5f}, C={c18['close']:.5f} (Bullish)")
