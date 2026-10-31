import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import track_key_levels
from slk_bot.data.candles import drop_forming

cache = CandleCache()

print("=" * 90)
print(" GOLDEN CHECKS REPORT")
print("=" * 90)

# ------------------------------------------------------------------------------
# Check 4a: AUDCAD weekly as of 2026-10-02 21:00 UTC
# ------------------------------------------------------------------------------
print("\n" + "-" * 90)
print(" 4a. AUDCAD Weekly as of 2026-10-02 21:00 UTC (Target near 0.98535, tol 0.05%)")
print("-" * 90)

df_w = cache.load_candles("AUDCAD", "W")
asof_a = pd.Timestamp("2026-10-02 21:00:00", tz="UTC")
closed_a = drop_forming(df_w, "W", "forex", as_of=asof_a).tail(104)
lvls_a = track_key_levels(closed_a, "W", "AUDCAD", lookback=104)

target_a = 0.98535
matched_a = [l for l in lvls_a if abs(l.price - target_a) / target_a <= 0.0005]

print(f"{'Kind':<14} | {'Price':<10} | {'Current Role':<13} | {'Disrespect':<10} | {'Unbroken?':<9} | {'Formed (UTC)':<16} | {'Diff %':<8}")
print(f"{'-'*14}-+-{'-'*10}-+-{'-'*13}-+-{'-'*10}-+-{'-'*9}-+-{'-'*16}-+-{'-'*8}")

for l in matched_a:
    unbroken = "YES" if l.is_unbroken else "NO"
    diff_pct = abs(l.price - target_a) / target_a * 100
    print(f"{l.kind.value:<14} | {l.price:.5f}    | {l.current_role.value:<13} | {l.disrespect_count:<10} | {unbroken:<9} | {l.formation_time.strftime('%Y-%m-%d %H:%M'):<16} | {diff_pct:.3f}%")

c_w_0927 = df_w.loc["2026-09-27 21:00:00+00:00"]
print(f"\nNormalized Weekly Candle opening 2026-09-27 21:00 UTC:")
print(f"  Bot: O={c_w_0927['open']:.5f}, H={c_w_0927['high']:.5f}, L={c_w_0927['low']:.5f}, C={c_w_0927['close']:.5f}")
print(f"  TV : O=0.99316, H=0.99695, L=0.98395, C=0.99123")


# ------------------------------------------------------------------------------
# Check 4b: AUDCAD daily as of 2026-10-08 21:00 UTC
# ------------------------------------------------------------------------------
print("\n" + "-" * 90)
print(" 4b. AUDCAD Daily as of 2026-10-08 21:00 UTC (Targets near 0.99467, 0.99412, 0.99264, tol 0.05%)")
print("-" * 90)

df_d_aud = cache.load_candles("AUDCAD", "D")
asof_b = pd.Timestamp("2026-10-08 21:00:00", tz="UTC")
closed_b = drop_forming(df_d_aud, "D", "forex", as_of=asof_b).tail(250)
lvls_b = track_key_levels(closed_b, "D", "AUDCAD", lookback=250)

targets_b = [
    ("0.99467", 0.99467),
    ("0.99412", 0.99412),
    ("0.99264", 0.99264),
]

for label, target in targets_b:
    print(f"\nTarget {label}:")
    matched = [l for l in lvls_b if abs(l.price - target) / target <= 0.0005 and l.kind.value == "A"]
    for l in matched:
        unbroken = "YES" if l.is_unbroken else "NO"
        diff_pct = abs(l.price - target) / target * 100
        print(f"  {l.kind.value:<12} | Price={l.price:.5f} | Role={l.current_role.value:<12} | Disrespect={l.disrespect_count:<2} | Unbroken={unbroken:<3} | Formed={l.formation_time.strftime('%Y-%m-%d %H:%M UTC')} | Diff={diff_pct:.3f}%")

c_d_1008 = df_d_aud.loc["2026-10-08 21:00:00+00:00"]
print(f"\nNormalized Daily Candle opening 2026-10-08 21:00 UTC:")
print(f"  Bot: O={c_d_1008['open']:.5f}, H={c_d_1008['high']:.5f}, L={c_d_1008['low']:.5f}, C={c_d_1008['close']:.5f}")
print(f"  TV : closes about 0.9956, above all three targets")


# ------------------------------------------------------------------------------
# Check 4c: XAGUSD daily as of 2026-10-09 21:00 UTC
# ------------------------------------------------------------------------------
print("\n" + "-" * 90)
print(" 4c. XAGUSD Daily as of 2026-10-09 21:00 UTC (Target near 61.366, tol 0.15%)")
print("-" * 90)

df_d_xag = cache.load_candles("XAGUSD", "D")
asof_c = pd.Timestamp("2026-10-09 21:00:00", tz="UTC")
closed_c = drop_forming(df_d_xag, "D", "commodity", as_of=asof_c).tail(250)
lvls_c = track_key_levels(closed_c, "D", "XAGUSD", lookback=250)

target_c = 61.366
matched_c = [l for l in lvls_c if abs(l.price - target_c) / target_c <= 0.0015 and l.kind.value == "A"]

for l in matched_c:
    unbroken = "YES" if l.is_unbroken else "NO"
    diff_pct = abs(l.price - target_c) / target_c * 100
    print(f"  {l.kind.value:<12} | Price={l.price:.5f} | Role={l.current_role.value:<12} | Disrespect={l.disrespect_count:<2} | Unbroken={unbroken:<3} | Formed={l.formation_time.strftime('%Y-%m-%d %H:%M UTC')} | Diff={diff_pct:.3f}%")

c1011 = df_d_xag.loc["2026-10-11 21:00:00+00:00"]
idx_1011 = df_d_xag.index.get_loc("2026-10-11 21:00:00+00:00")
c_prev = df_d_xag.iloc[idx_1011 - 1]

print(f"\nDaily candle before 2026-10-11 ({c_prev.name.strftime('%Y-%m-%d %H:%M UTC')}):")
print(f"  O={c_prev['open']:.4f}, H={c_prev['high']:.4f}, L={c_prev['low']:.4f}, C={c_prev['close']:.4f} (Bullish: C > O)")
print(f"Daily candle opening 2026-10-11 21:00 UTC:")
print(f"  O={c1011['open']:.4f}, H={c1011['high']:.4f}, L={c1011['low']:.4f}, C={c1011['close']:.4f}")
print(f"  Confirmation: High {c1011['high']:.4f} > Prev High {c_prev['high']:.4f}, and Close {c1011['close']:.4f} < 61.366")


# ------------------------------------------------------------------------------
# Check 4d: XAGUSD H4 as of 2026-10-11 21:00 UTC and 2026-10-12 05:00 UTC
# ------------------------------------------------------------------------------
print("\n" + "-" * 90)
print(" 4d. XAGUSD H4 as of 2026-10-11 21:00 UTC & 2026-10-12 05:00 UTC (Target near 60.811, tol 0.15%)")
print("-" * 90)

df_h4_xag = cache.load_candles("XAGUSD", "H4")
target_d = 60.811

for asof_str in ["2026-10-11 21:00:00", "2026-10-12 05:00:00"]:
    asof_d = pd.Timestamp(asof_str, tz="UTC")
    closed_d = drop_forming(df_h4_xag, "H4", "commodity", as_of=asof_d).tail(300)
    lvls_d = track_key_levels(closed_d, "H4", "XAGUSD", lookback=300)
    print(f"\nAs of {asof_str} UTC:")
    matched_all = [l for l in lvls_d if abs(l.price - target_d) / target_d <= 0.0015]
    if matched_all:
        for l in matched_all:
            unbroken = "YES" if l.is_unbroken else "NO"
            diff_pct = abs(l.price - target_d) / target_d * 100
            print(f"  {l.kind.value:<12} | Price={l.price:.5f} | Role={l.current_role.value:<12} | Disrespect={l.disrespect_count:<2} | Unbroken={unbroken:<3} | Formed={l.formation_time.strftime('%Y-%m-%d %H:%M UTC')} | Diff={diff_pct:.3f}%")
    else:
        print("  No level found within 0.15%.")

print("\n" + "=" * 90)
