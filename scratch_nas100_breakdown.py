from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import apply_open_continuity
o = OandaProvider()
d = o.get_candles('NAS100_USD', 'D', count=20)
raw = d.loc['2026-10-11 21:00:00+00:00':'2026-10-15 21:00:00+00:00']
norm = apply_open_continuity(d).loc['2026-10-11 21:00:00+00:00':'2026-10-15 21:00:00+00:00']

days = ["Mon Oct 12", "Tue Oct 13", "Wed Oct 14", "Thu Oct 15", "Fri Oct 16"]

print("=== NAS100 DAILY CANDLES: RAW (OCT 12 - OCT 16) ===")
for (ts, r), day in zip(raw.iterrows(), days):
    t_str = ts.strftime("%Y-%m-%d %H:%M UTC")
    print(f"{day} ({t_str}) | Open: {r['open']:8.2f} | High: {r['high']:8.2f} | Low: {r['low']:8.2f} | Close: {r['close']:8.2f}")

print("\n=== NAS100 DAILY CANDLES: NORMALIZED (OCT 12 - OCT 16) ===")
for (ts, r), day in zip(norm.iterrows(), days):
    t_str = ts.strftime("%Y-%m-%d %H:%M UTC")
    print(f"{day} ({t_str}) | Open: {r['open']:8.2f} | High: {r['high']:8.2f} | Low: {r['low']:8.2f} | Close: {r['close']:8.2f}")

print("\n=== HOW THE WEEKLY CANDLE IS BUILT ===")
print(f"Weekly Open  = Monday Open (normalized) = {norm.iloc[0]['open']:.2f}")
print(f"Weekly High  = Max(Highs)               = {norm['high'].max():.2f}  (hit on Monday)")
print(f"Weekly Low   = Min(Lows)                = {norm['low'].min():.2f}  (hit on Friday)")
print(f"Weekly Close = Friday Close             = {norm.iloc[-1]['close']:.2f}  (Friday session close)")

# Partial week through Thursday:
norm_thru_thu = norm.iloc[:4]
print("\n=== PARTIAL WEEK AS OF THURSDAY CLOSE (OCT 15) ===")
print(f"Partial High = {norm_thru_thu['high'].max():.2f}")
print(f"Partial Low  = {norm_thru_thu['low'].min():.2f}   <-- Exactly the 29439.8 from earlier report!")
print(f"Partial Close= {norm_thru_thu.iloc[-1]['close']:.2f}   <-- Exactly the 29906.6 from earlier report!")
