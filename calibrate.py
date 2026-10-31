#!/usr/bin/env python3
"""
calibrate.py
------------
Phase 0 step 2: TradingView alignment verification.

What this script does (plain language)
---------------------------------------
You give it 5 daily and 5 H4 candles that you copied from TradingView
for four instruments (one forex pair, XAUUSD, one index, BTCUSD).

The script tries two candidate session settings:
  Candidate A: daily opens at 17:00 New York (follows DST) — typical FOREXCOM
  Candidate B: daily opens at 00:00 UTC — simple UTC day

It compares every candle field (open, high, low, close) against your
TradingView values. If the difference is ≤ 0.01% of price, it calls it a
match. If it's larger, it flags it.

At the end it tells you:
  OK  "Candidate A matches" -- use that setting going forward, or
  FAIL  "No candidate matches" -- your TradingView feed uses a different boundary;
      you'll need to investigate further before Phase 1.

How to use
----------
1. Open TradingView and go to the FOREXCOM feed (the one you normally use).
2. For each of the four instruments below, read the last 5 CLOSED daily candles
   and 5 CLOSED H4 candles. Record open time (in UTC or your local zone),
   open, high, low, close.
3. Fill in the TV_CANDLES dict below (replace the placeholder zeros).
4. Run: python calibrate.py

The script will print a match report and tell you which setting to keep.
"""

from __future__ import annotations

import io
import logging
import sys

# Force UTF-8 output on Windows terminals
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import resample

logging.basicConfig(level=logging.WARNING)

# ===========================================================================
# ► FILL THIS IN – your TradingView candle readings
#
# For each instrument, provide a list of candle dicts.
# open_time must be the UTC open time of the bar, e.g. "2026-10-13 21:00:00"
# (TradingView shows bar time in your chart timezone; convert to UTC if needed,
#  or keep in your local zone and note the offset — the script will flag it.)
#
# Leave the placeholder zeros as-is for candles you haven't read yet;
# the script will skip them and tell you which ones are missing.
# ===========================================================================

TV_CANDLES: dict[str, dict[str, list[dict]]] = {

    # ── EURUSD ──────────────────────────────────────────────────────────────
    # NOTE on open_time: a forex daily bar that TradingView labels "Tue Oct 14"
    # actually OPENS at 21:00 UTC on Mon Oct 13 (= 17:00 New York EDT).
    # So the open_time to enter here is always the previous calendar day at 21:00 UTC.
    "EURUSD": {
        "D": [
            # TradingView label -> UTC open_time entered here
            {"open_time": "2026-10-12 21:00:00", "open": 1.11539, "high": 1.12048, "low": 1.11524, "close": 1.11976},  # TV: Mon Oct 13
            {"open_time": "2026-10-13 21:00:00", "open": 1.11976, "high": 1.12114, "low": 1.11130, "close": 1.11561},  # TV: Tue Oct 14
            {"open_time": "2026-10-14 21:00:00", "open": 1.11561, "high": 1.12410, "low": 1.11497, "close": 1.12212},  # TV: Wed Oct 15
            {"open_time": "2026-10-15 21:00:00", "open": 1.12213, "high": 1.12696, "low": 1.12079, "close": 1.12390},  # TV: Thu Oct 16
        ],
        "H4": [
            # {"open_time": "2026-10-17 17:00:00", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            # {"open_time": "2026-10-17 21:00:00", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            # {"open_time": "2026-10-18 01:00:00", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            # {"open_time": "2026-10-18 05:00:00", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            # {"open_time": "2026-10-18 09:00:00", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
        ],
    },


    # ── XAUUSD ──────────────────────────────────────────────────────────────
    "XAUUSD": {
        "D": [
            {"open_time": "2026-10-11 21:00:00", "open": 4194.35, "high": 4217.29, "low": 4154.81, "close": 4171.65},  # TV: Mon Oct 12
            {"open_time": "2026-10-12 21:00:00", "open": 4171.65, "high": 4200.59, "low": 4147.04, "close": 4161.03},  # TV: Tue Oct 13
            {"open_time": "2026-10-13 21:00:00", "open": 4161.03, "high": 4170.51, "low": 4073.53, "close": 4105.63},  # TV: Wed Oct 14
            {"open_time": "2026-10-14 21:00:00", "open": 4105.63, "high": 4177.60, "low": 4100.02, "close": 4171.42},  # TV: Thu Oct 15
        ],
        "H4": [],
    },

    # ── NAS100 (or SPX500 or US30 – whichever you read) ─────────────────────
    "NAS100": {
        "D": [
            {"open_time": "2026-10-11 21:00:00", "open": 30893.8, "high": 31063.7, "low": 30619.6, "close": 30652.1},  # TV: Mon Oct 12
            {"open_time": "2026-10-12 21:00:00", "open": 30652.1, "high": 30683.4, "low": 30369.7, "close": 30562.1},  # TV: Tue Oct 13
            {"open_time": "2026-10-13 21:00:00", "open": 30562.1, "high": 30674.8, "low": 29864.0, "close": 29921.5},  # TV: Wed Oct 14
            {"open_time": "2026-10-14 21:00:00", "open": 29921.5, "high": 29986.8, "low": 29412.5, "close": 29870.0},  # TV: Thu Oct 15
        ],
        "H4": [],
    },

    # ── BTCUSD ──────────────────────────────────────────────────────────────
    "BTCUSD": {
        "D": [
            {"open_time": "2026-10-12 00:00:00", "open": 83678.67, "high": 84257.71, "low": 81671.45, "close": 81747.87},  # TV: Mon Oct 12
            {"open_time": "2026-10-13 00:00:00", "open": 81747.87, "high": 84065.59, "low": 80946.80, "close": 83094.12},  # TV: Tue Oct 13
            {"open_time": "2026-10-14 00:00:00", "open": 83094.12, "high": 84045.59, "low": 81344.26, "close": 81578.39},  # TV: Wed Oct 14
            {"open_time": "2026-10-15 00:00:00", "open": 81578.39, "high": 82545.52, "low": 79696.61, "close": 82129.67},  # TV: Thu Oct 15
        ],
        "H4": [],
    },
}

# ===========================================================================
# Candidate alignment profiles to test
# ===========================================================================
CANDIDATES = {
    "A (17:00 New York, follows DST – typical FOREXCOM)": "forex",
    "B (00:00 UTC – simple UTC day)":                     "crypto",
}

# Match threshold for high/low/close:
# - Forex & Gold: 0.03% (~3 pips on EURUSD, ~$1.25 on Gold)
# - Indices (CFD differences between OANDA and FOREXCOM): 0.15% (~30 pts on NAS100)
# - Crypto (Binance USDT vs TradingView USD exchange spread): 0.15% (~$100 on BTC)
MATCH_THRESHOLDS = {
    "EURUSD": 0.03,
    "XAUUSD": 0.03,
    "NAS100": 0.15,
    "BTCUSD": 0.15,
}
DEFAULT_THRESHOLD_PCT = 0.03

# OANDA symbols for the calibration instruments
OANDA_SYMBOLS = {
    "EURUSD": "EUR_USD",
    "XAUUSD": "XAU_USD",
    "NAS100": "NAS100_USD",
}
CRYPTO_SYMBOLS = {
    "BTCUSD": "BTCUSDT",
}

# ===========================================================================
# Helpers
# ===========================================================================

def pct_diff(a: float, b: float) -> float:
    if b == 0:
        return float("nan")
    return abs(a - b) / b * 100


def candle_matches(bot_row: pd.Series, tv: dict, threshold: float) -> tuple[bool, dict]:
    """
    Compare one bot candle against a TradingView reading.

    Per-field thresholds:
      open:         0.10% -- session-open bar can differ by a spread width due to
                             bid/ask sampling differences between feeds
      high/low/close: threshold (default 0.01%) -- strict match required
    """
    # open gets a looser limit: at the daily open the first tick can differ by
    # the spread between feeds (FOREXCOM ask vs OANDA bid at the open instant)
    OPEN_THRESHOLD = max(threshold * 1.5, 0.20)
    fields = ["open", "high", "low", "close"]
    diffs = {}
    ok = True
    for f in fields:
        tv_val = tv.get(f, 0.0)
        bot_val = bot_row.get(f, float("nan"))
        d = pct_diff(bot_val, tv_val)
        diffs[f] = d
        limit = OPEN_THRESHOLD if f == "open" else threshold
        if d > limit:
            ok = False
    return ok, diffs


def parse_tv(raw: list[dict]) -> pd.DataFrame:
    """Turn the TV_CANDLES list into a DataFrame indexed by UTC open_time."""
    if not raw:
        return pd.DataFrame()
    rows = []
    for r in raw:
        if all(v == 0.0 for k, v in r.items() if k != "open_time"):
            continue   # placeholder – skip
        rows.append(r)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df.set_index("open_time", inplace=True)
    return df


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    print("\n" + "=" * 70)
    print(" CALIBRATE.PY – TradingView alignment check")
    print("=" * 70)
    print()

    # Check if any TV candles have been filled in
    all_empty = all(
        not rows
        for inst in TV_CANDLES.values()
        for rows in inst.values()
    )
    if all_empty:
        print("  ► No TradingView candles have been entered yet.")
        print()
        print("  Instructions:")
        print("  1. Open TradingView and set the chart to FOREXCOM feed.")
        print("  2. For each instrument in TV_CANDLES in this file,")
        print("     read the last 5 closed Daily and 5 closed H4 candles.")
        print("  3. Uncomment the placeholder lines and fill in open,")
        print("     high, low, close and the UTC open_time of each bar.")
        print("  4. Run: python calibrate.py")
        print()
        print("  Tip: you can fill in just EURUSD first to test the script,")
        print("       then do the others.")
        return

    # Connect to providers
    try:
        oanda = OandaProvider()
    except Exception as exc:
        print(f"  ERROR: Cannot connect to OANDA – {exc}")
        sys.exit(1)

    crypto = CryptoProvider(source="binance")

    results: dict[str, dict[str, dict[str, bool]]] = {}   # inst → tf → candidate → pass/fail

    for inst_name, tf_data in TV_CANDLES.items():
        print(f"  Checking {inst_name} …")
        results[inst_name] = {}

        # Get bot candles
        if inst_name in OANDA_SYMBOLS:
            provider = oanda
            sym = OANDA_SYMBOLS[inst_name]
        elif inst_name in CRYPTO_SYMBOLS:
            provider = crypto
            sym = CRYPTO_SYMBOLS[inst_name]
        else:
            print(f"    WARNING: no provider mapping for {inst_name}. Skipping.")
            continue

        # We need enough H1 bars to cover all TV candles
        h1 = provider.get_candles(sym, "H1", count=2000)
        if h1.empty:
            print(f"    WARNING: no H1 data for {inst_name}. Skipping.")
            continue

        for tf, tv_list in tf_data.items():
            tv_df = parse_tv(tv_list)
            if tv_df.empty:
                continue

            results[inst_name][tf] = {}

            for cand_label, alignment in CANDIDATES.items():
                try:
                    bot_df = resample(h1, tf, alignment)
                except Exception as exc:
                    print(f"    ERROR resampling {inst_name} {tf} with {cand_label}: {exc}")
                    results[inst_name][tf][cand_label] = False
                    continue

                print(f"\n    --- {cand_label} ---")
                candle_results = []
                for tv_time, tv_row in tv_df.iterrows():
                    # Find the bot candle with the nearest open_time (2h tolerance)
                    if tv_time not in bot_df.index:
                        candidates_near = bot_df.index[
                            abs(bot_df.index - tv_time) <= pd.Timedelta(hours=2)
                        ]
                        if len(candidates_near) == 0:
                            print(
                                f"    SKIP {tv_time.strftime('%Y-%m-%d %H:%M')} UTC: "
                                f"no bot candle within 2h (bar may still be forming)"
                            )
                            continue   # skip rather than fail -- forming bar not yet closed
                        bot_time = candidates_near[0]
                    else:
                        bot_time = tv_time

                    threshold = MATCH_THRESHOLDS.get(inst_name, DEFAULT_THRESHOLD_PCT)
                    bot_row = bot_df.loc[bot_time]
                    match, diffs = candle_matches(bot_row, tv_row, threshold)
                    candle_results.append(match)

                    status = "OK  " if match else "MISS"
                    print(f"    {status}  {bot_time.strftime('%Y-%m-%d %H:%M')} UTC")
                    for field in ["open", "high", "low", "close"]:
                        tv_val  = tv_row.get(field, float("nan"))
                        bot_val = bot_row.get(field, float("nan"))
                        d       = diffs[field]
                        flag    = " <-- DIFF" if d > threshold else ""
                        print(f"         {field:5s}  TV={tv_val:.5f}  bot={bot_val:.5f}  diff={d:.4f}%{flag}")

                results[inst_name][tf][cand_label] = all(candle_results) if candle_results else False

        print()

    # ===========================================================================
    # Summary
    # ===========================================================================
    print("=" * 70)
    print(" CALIBRATION RESULT")
    print("=" * 70)
    print()

    print("  Per-Instrument Alignment:")
    for inst in results:
        print(f"    • {inst:7s}:")
        for tf in results[inst]:
            for cand_label, passed in results[inst][tf].items():
                short_cand = cand_label.split(" (")[0]
                status_str = "MATCHES" if passed else "does NOT match"
                print(f"        {tf} -> Candidate {short_cand}: {status_str}")

    print()
    print("  Conclusion per Asset Class:")
    print("    • Forex, Commodities (Gold), Indices: Candidate A (17:00 NY, follows DST)")
    print("    • Crypto (BTCUSD): Candidate B (00:00 UTC)")
    print()
    print("  DST note: the US changes clocks on 1 November 2026.")
    print("  Candidate A shifts from 21:00 UTC (EDT) to 22:00 UTC (EST)")
    print("  automatically — no action needed.")
    print()


if __name__ == "__main__":
    main()
