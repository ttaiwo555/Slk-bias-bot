"""
analysis/run_determinism_test.py
--------------------------------
Runs the full parity match twice back-to-back:
  1. Produces match_table_run1.csv and match_table_run2.csv
  2. Computes and prints their SHA256 hashes to demonstrate determinism
  3. Writes analysis/output/run_manifest.json and run_manifest.txt containing:
     - Config values
     - Last candle timestamp per symbol and timeframe
     - File hashes
"""
import sys
import hashlib
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.run_parity_reproduction import run_parity, OUTPUT_DIR
from slk_bot.storage.cache import CandleCache

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def main():
    print("=" * 80)
    print("ITEM 4: DETERMINISM TEST (TWO BACK-TO-BACK RUNS)")
    print("=" * 80)

    # Run 1
    print("Running Run 1...")
    m1_df, e1_df = run_parity("first_touch")
    run1_path = OUTPUT_DIR / "match_table_run1.csv"
    m1_df.to_csv(run1_path, index=False)
    hash1 = sha256_file(run1_path)
    print(f"Run 1 saved to: {run1_path}")
    print(f"Run 1 SHA256  : {hash1}")

    # Run 2
    print("\nRunning Run 2...")
    m2_df, e2_df = run_parity("first_touch")
    run2_path = OUTPUT_DIR / "match_table_run2.csv"
    m2_df.to_csv(run2_path, index=False)
    hash2 = sha256_file(run2_path)
    print(f"Run 2 saved to: {run2_path}")
    print(f"Run 2 SHA256  : {hash2}")

    # Canonical match_table.csv
    canonical_path = OUTPUT_DIR / "match_table.csv"
    m2_df.to_csv(canonical_path, index=False)
    canon_hash = sha256_file(canonical_path)

    identical = (hash1 == hash2)
    print("\n" + "=" * 80)
    print(f"DETERMINISM RESULT: {'IDENTICAL (PASS)' if identical else 'DIFFERENT (FAIL)'}")
    print(f"Run 1 Hash: {hash1}")
    print(f"Run 2 Hash: {hash2}")
    print("=" * 80)

    # Generate Run Manifest
    cache = CandleCache()
    ref_df = pd.read_csv(ROOT / "reference_setups.csv")
    symbols = sorted(ref_df["symbol"].unique())

    with open(ROOT / "slk_bot" / "config" / "settings.yaml", "r", encoding="utf-8") as f:
        settings_yaml = yaml.safe_load(f)

    last_timestamps = {}
    candle_counts = {}
    for sym in symbols:
        last_timestamps[sym] = {}
        candle_counts[sym] = {}
        for tf in ["D", "H4", "W"]:
            df = cache.load_candles(sym, tf)
            if not df.empty:
                last_timestamps[sym][tf] = str(df.index[-1])
                candle_counts[sym][tf] = len(df)
            else:
                last_timestamps[sym][tf] = None
                candle_counts[sym][tf] = 0

    manifest = {
        "parity_profile": "reference_parity",
        "config_values": {
            "external_reference_point": "first_touch",
            "lookback": None,
            "max_disrespects": None,
            "ocl_max_disrespects": None,
            "primary_selection_rule": "fresh_first",
            "doji_body_threshold_pct": 0.0,
            "enable_invalidation": True,
            "trend_candidate": settings_yaml.get("strategy", {}).get("trend_candidate", "c"),
            "trend_evaluation_moment": settings_yaml.get("strategy", {}).get("trend_evaluation_moment", "alert_time"),
        },
        "file_hashes": {
            "match_table.csv": canon_hash,
            "match_table_run1.csv": hash1,
            "match_table_run2.csv": hash2,
            "reference_setups.csv": sha256_file(ROOT / "reference_setups.csv"),
        },
        "candle_counts": candle_counts,
        "last_candle_timestamps": last_timestamps,
    }

    manifest_json_path = OUTPUT_DIR / "run_manifest.json"
    manifest_txt_path = OUTPUT_DIR / "run_manifest.txt"

    with open(manifest_json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    with open(manifest_txt_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("RUN MANIFEST (analysis/output/run_manifest.txt)\n")
        f.write("=" * 80 + "\n\n")
        f.write("CONFIG VALUES:\n")
        for k, v in manifest["config_values"].items():
            f.write(f"  {k:<28}: {v}\n")
        f.write("\nFILE HASHES (SHA256):\n")
        for k, v in manifest["file_hashes"].items():
            f.write(f"  {k:<28}: {v}\n")
        f.write("\nCACHED CANDLES PER SYMBOL & TIMEFRAME:\n")
        f.write(f"  {'Symbol':<10} | {'D Count':>8} | {'H4 Count':>8} | {'W Count':>8} | {'Last D Timestamp':<30}\n")
        f.write("  " + "-" * 75 + "\n")
        for sym in symbols:
            d_c = candle_counts[sym].get("D", 0)
            h4_c = candle_counts[sym].get("H4", 0)
            w_c = candle_counts[sym].get("W", 0)
            last_d = str(last_timestamps[sym].get("D", ""))
            f.write(f"  {sym:<10} | {d_c:>8} | {h4_c:>8} | {w_c:>8} | {last_d:<30}\n")

    print(f"\nManifest written to:")
    print(f"  - {manifest_json_path}")
    print(f"  - {manifest_txt_path}")

if __name__ == "__main__":
    main()
