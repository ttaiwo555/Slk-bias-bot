"""
analysis/verify_counts.py
-------------------------
Verifies and prints every count from analysis/output/match_table.csv:
- Shows file path and modification time
- Status breakdown (MATCH, PARTIAL, MISSING)
- Key level kind match vs mismatch on MATCH rows
- Key level price difference on MATCH rows (<= 0.03% vs > 0.03%)
- Rows whose breakout candle time equals the reference
- Prints full 35-row table
"""
import os
import datetime
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
match_file = ROOT / "analysis" / "output" / "match_table.csv"
ref_file = ROOT / "reference_setups.csv"

stat = os.stat(match_file)
mtime_str = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

df_match = pd.read_csv(match_file)
df_ref = pd.read_csv(ref_file)

print("=" * 80)
print(f"VERIFYING COUNTS FROM: {match_file.resolve()}")
print(f"File Modification Time: {mtime_str}")
print(f"File Size: {stat.st_size} bytes")
print("=" * 80)
print(f"Total rows in match table: {len(df_match)}")

status_counts = df_match["status"].value_counts()
print("\nSTATUS COUNTER:")
for status in ["MATCH", "PARTIAL", "MISSING"]:
    count = status_counts.get(status, 0)
    print(f"  {status:<10}: {count:>2} ({count / len(df_match) * 100:.1f}%)")

matches = df_match[df_match["status"] == "MATCH"].copy()
print(f"\nTOTAL MATCH ROWS: {len(matches)}")

# Compare key level kind and price on MATCH rows
kind_matches = 0
kind_mismatches = []
price_diffs_le_03 = 0
price_diffs_gt_03 = []

for _, row in matches.iterrows():
    r_id = int(row["id"])
    sym = row["symbol"]
    r_row = df_ref[df_ref["id"] == r_id].iloc[0]

    r_kind_raw = str(row["key_kind_ref"])
    o_kind_raw = str(row["key_kind_ours"])
    r_kind = "OCL" if "OCL" in r_kind_raw else r_kind_raw
    o_kind = "OCL" if "OCL" in o_kind_raw else o_kind_raw

    if r_kind == o_kind:
        kind_matches += 1
    else:
        kind_mismatches.append((r_id, sym, r_kind_raw, o_kind_raw))

    r_price = float(r_row["kl_price"])
    o_price = float(row["our_level_price"])
    diff_pct = abs(o_price - r_price) / r_price * 100.0

    if diff_pct <= 0.03:
        price_diffs_le_03 += 1
    else:
        price_diffs_gt_03.append((r_id, sym, r_price, o_price, diff_pct))

print(f"\nKEY LEVEL KIND ON MATCH ROWS:")
print(f"  Same Key Kind:      {kind_matches} / {len(matches)} ({kind_matches / len(matches) * 100:.1f}%)")
print(f"  Different Key Kind: {len(kind_mismatches)} / {len(matches)} ({len(kind_mismatches) / len(matches) * 100:.1f}%)")
if kind_mismatches:
    for m in kind_mismatches:
        print(f"    # {m[0]} {m[1]}: Reference={m[2]} vs Ours={m[3]}")

print(f"\nKEY LEVEL PRICE DIFFERENCE ON MATCH ROWS:")
print(f"  Price Diff <= 0.03%: {price_diffs_le_03} / {len(matches)} ({price_diffs_le_03 / len(matches) * 100:.1f}%)")
print(f"  Price Diff > 0.03%:  {len(price_diffs_gt_03)} / {len(matches)} ({len(price_diffs_gt_03) / len(matches) * 100:.1f}%)")
if price_diffs_gt_03:
    for p in price_diffs_gt_03:
        print(f"    # {p[0]} {p[1]}: Ref={p[2]:.5f}, Ours={p[3]:.5f}, Diff={p[4]:.4f}%")

# Breakout candle time equality
bo_time_matches = 0
bo_time_match_ids = []
for _, row in df_match.iterrows():
    r_id = int(row["id"])
    r_row = df_ref[df_ref["id"] == r_id].iloc[0]
    ref_bo_t = str(r_row["bo_open_utc"]).replace(" ", "T")
    our_bo_t = str(row["our_bo_time"]).replace(" ", "T") if pd.notna(row["our_bo_time"]) else ""
    if our_bo_t and ref_bo_t and our_bo_t == ref_bo_t:
        bo_time_matches += 1
        bo_time_match_ids.append(r_id)

print(f"\nBREAKOUT CANDLE TIME EQUALS REFERENCE:")
print(f"  Matching Breakout Time: {bo_time_matches} / {len(df_match)} ({bo_time_matches / len(df_match) * 100:.1f}%)")
print(f"  Matching Setup IDs: {bo_time_match_ids}")

print("\n" + "=" * 120)
print(f"{'ID':>2} {'Symbol':>7} | {'Status':>7} | {'Ref Kind':>8} {'Our Kind':>12} | {'Ref KL':>10} {'Our KL':>10} | {'Ref BO P':>10} {'Our BO P':>10} | {'Ref BO Time':>16} {'Our BO Time':>16}")
print("-" * 120)
for _, row in df_match.iterrows():
    r_id = int(row["id"])
    sym = row["symbol"]
    r_row = df_ref[df_ref["id"] == r_id].iloc[0]
    status = row["status"]
    r_k = str(row["key_kind_ref"])
    o_k = str(row["key_kind_ours"]) if pd.notna(row["key_kind_ours"]) else "-"
    r_kl = f"{float(r_row['kl_price']):.5f}"
    o_kl = f"{float(row['our_level_price']):.5f}" if (pd.notna(row['our_level_price']) and str(row['our_level_price']) != '') else "-"
    r_bo = f"{float(r_row['bo_price']):.5f}"
    o_bo = f"{float(row['our_bo_price']):.5f}" if (pd.notna(row['our_bo_price']) and str(row['our_bo_price']) != '') else "-"
    r_bot = str(r_row['bo_open_utc'])
    o_bot = str(row['our_bo_time']) if (pd.notna(row['our_bo_time']) and str(row['our_bo_time']) != '') else "-"
    print(f"{r_id:>2} {sym:>7} | {status:>7} | {r_k:>8} {o_k:>12} | {r_kl:>10} {o_kl:>10} | {r_bo:>10} {o_bo:>10} | {r_bot:>16} {o_bot:>16}")
print("=" * 120)
