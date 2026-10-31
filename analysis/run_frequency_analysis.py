"""
run_frequency_analysis.py
-------------------------
Calculate HTF trigger frequency across all instruments:
- Daily: last 12 months (e.g. 2025-10-19 to 2026-10-19)
- Weekly: last 24 months (e.g. 2024-10-19 to 2026-10-19)
Triggers per method (1, 2, 3), total, and average per month.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path
import pandas as pd
import yaml
from dotenv import load_dotenv

# Force UTF-8 on Windows
if sys.platform == "win32":
    import os
    os.system("chcp 65001 > nul")

load_dotenv()

from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.models import TriggerMethod
from slk_bot.replay import load_settings_thresholds, load_doji_body_threshold_pct

ROOT = Path(__file__).resolve().parent
INST_PATH = ROOT / "slk_bot" / "config" / "instruments.yaml"
SETTINGS_PATH = ROOT / "slk_bot" / "config" / "settings.yaml"

with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
    SETTINGS_DATA = yaml.safe_load(f)

cache = CandleCache()
oanda = OandaProvider()
crypto = CryptoProvider()

thresholds = load_settings_thresholds()
doji_pct = load_doji_body_threshold_pct()


def ensure_data(inst: dict) -> None:
    name = inst["name"]
    asset_class = inst.get("asset_class", "forex")
    align = SETTINGS_DATA.get("alignment", {}).get(inst.get("alignment", "forex"), {})
    open_cont = align.get("open_continuity", True)

    # Check if D and W already in cache
    df_d = cache.load_candles(name, "D")
    df_w = cache.load_candles(name, "W")

    if len(df_d) >= 300 and len(df_w) >= 120:
        return

    print(f"Fetching D & W for {name} ({asset_class})...")
    if asset_class == "crypto":
        pair = inst.get("binance_symbol") or inst.get("bybit_symbol")
        candles_d = crypto.get_candles(pair, "D", count=350)
        candles_w = crypto.get_candles(pair, "W", count=150)
    else:
        oanda_sym = inst.get("oanda_symbol")
        if not oanda_sym:
            return
        candles_d = oanda.get_candles(oanda_sym, "D", count=350)
        candles_w = oanda.get_candles(oanda_sym, "W", count=150)

    if candles_d is not None and not candles_d.empty:
        if open_cont:
            candles_d = apply_open_continuity(candles_d)
        cache.save_candles(name, "D", candles_d)

    if candles_w is not None and not candles_w.empty:
        if open_cont:
            candles_w = apply_open_continuity(candles_w)
        cache.save_candles(name, "W", candles_w)


def analyze_frequency():
    instruments = INST_DATA.get("instruments", [])
    print(f"Ensuring data for all {len(instruments)} instruments...")
    for inst in instruments:
        try:
            ensure_data(inst)
        except Exception as e:
            print(f"Error fetching {inst['name']}: {e}")

    # Set evaluation windows:
    # Anchor date: 2026-10-19
    end_date = pd.Timestamp("2026-10-19 23:59:59", tz="UTC")
    start_d_12m = pd.Timestamp("2025-10-19 00:00:00", tz="UTC")
    start_w_24m = pd.Timestamp("2024-10-19 00:00:00", tz="UTC")

    months_d = 12.0
    months_w = 24.0

    daily_results = []
    weekly_results = []

    print("\nEvaluating HTF triggers across all instruments...")

    for inst in instruments:
        name = inst["name"]
        asset_class = inst.get("asset_class", "forex")

        # --- Daily ---
        df_d = cache.load_candles(name, "D")
        if not df_d.empty:
            triggers_d = detect_triggers(
                df=df_d,
                timeframe="D",
                symbol=name,
                asset_class=asset_class,
                lookback=250,
                max_disrespects=3,
                doji_body_threshold_pct=doji_pct,
                marginal_thresholds=thresholds,
            )
            # Filter to last 12 months
            t_d_12m = [t for t in triggers_d if start_d_12m <= t.trigger_candle_time <= end_date]
            m1_d = sum(1 for t in t_d_12m if t.method == TriggerMethod.METHOD_1)
            m2_d = sum(1 for t in t_d_12m if t.method == TriggerMethod.METHOD_2)
            m3_d = sum(1 for t in t_d_12m if t.method == TriggerMethod.METHOD_3)
            tot_d = len(t_d_12m)
            avg_d = tot_d / months_d

            daily_results.append({
                "symbol": name,
                "asset_class": asset_class,
                "m1": m1_d,
                "m2": m2_d,
                "m3": m3_d,
                "total": tot_d,
                "avg_per_month": avg_d,
            })

        # --- Weekly ---
        df_w = cache.load_candles(name, "W")
        if not df_w.empty:
            triggers_w = detect_triggers(
                df=df_w,
                timeframe="W",
                symbol=name,
                asset_class=asset_class,
                lookback=104,
                max_disrespects=3,
                doji_body_threshold_pct=doji_pct,
                marginal_thresholds=thresholds,
            )
            # Filter to last 24 months
            t_w_24m = [t for t in triggers_w if start_w_24m <= t.trigger_candle_time <= end_date]
            m1_w = sum(1 for t in t_w_24m if t.method == TriggerMethod.METHOD_1)
            m2_w = sum(1 for t in t_w_24m if t.method == TriggerMethod.METHOD_2)
            m3_w = sum(1 for t in t_w_24m if t.method == TriggerMethod.METHOD_3)
            tot_w = len(t_w_24m)
            avg_w = tot_w / months_w

            weekly_results.append({
                "symbol": name,
                "asset_class": asset_class,
                "m1": m1_w,
                "m2": m2_w,
                "m3": m3_w,
                "total": tot_w,
                "avg_per_month": avg_w,
            })

    df_res_d = pd.DataFrame(daily_results)
    df_res_w = pd.DataFrame(weekly_results)

    # Save to report file
    out_path = ROOT / "frequency_report.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("=" * 85 + "\n")
        f.write(" HTF TRIGGER FREQUENCY REPORT\n")
        f.write(f" Generated: 2026-10-19 | Daily: Last 12 Months | Weekly: Last 24 Months\n")
        f.write("=" * 85 + "\n\n")

        f.write("--- DAILY TIMEFRAME (LAST 12 MONTHS: 2025-10-19 to 2026-10-19) ---\n")
        f.write(f"{'Symbol':<10} | {'Class':<11} | {'Method 1':<9} | {'Method 2':<9} | {'Method 3':<9} | {'Total':<7} | {'Avg/Mo':<8}\n")
        f.write("-" * 80 + "\n")
        for _, r in df_res_d.iterrows():
            f.write(f"{r['symbol']:<10} | {r['asset_class']:<11} | {r['m1']:<9} | {r['m2']:<9} | {r['m3']:<9} | {r['total']:<7} | {r['avg_per_month']:<8.2f}\n")
        f.write("-" * 80 + "\n")
        f.write(f"{'PORTFOLIO':<10} | {'TOTAL':<11} | {df_res_d['m1'].sum():<9} | {df_res_d['m2'].sum():<9} | {df_res_d['m3'].sum():<9} | {df_res_d['total'].sum():<7} | {df_res_d['avg_per_month'].sum():<8.2f}\n")

        f.write("\n\n--- WEEKLY TIMEFRAME (LAST 24 MONTHS: 2024-10-19 to 2026-10-19) ---\n")
        f.write(f"{'Symbol':<10} | {'Class':<11} | {'Method 1':<9} | {'Method 2':<9} | {'Method 3':<9} | {'Total':<7} | {'Avg/Mo':<8}\n")
        f.write("-" * 80 + "\n")
        for _, r in df_res_w.iterrows():
            f.write(f"{r['symbol']:<10} | {r['asset_class']:<11} | {r['m1']:<9} | {r['m2']:<9} | {r['m3']:<9} | {r['total']:<7} | {r['avg_per_month']:<8.2f}\n")
        f.write("-" * 80 + "\n")
        f.write(f"{'PORTFOLIO':<10} | {'TOTAL':<11} | {df_res_w['m1'].sum():<9} | {df_res_w['m2'].sum():<9} | {df_res_w['m3'].sum():<9} | {df_res_w['total'].sum():<7} | {df_res_w['avg_per_month'].sum():<8.2f}\n")

    print(f"Report written to {out_path}")


if __name__ == "__main__":
    analyze_frequency()
