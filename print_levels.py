#!/usr/bin/env python3
"""
print_levels.py
---------------
Phase 1 Checkpoint Deliverable & Key Levels Inspector.

Prints active and retired key levels inside the lookback window for:
  - EURUSD, XAUUSD, XAGUSD, NAS100, BTCUSD (or custom --symbol)
  - Across Weekly (W), Daily (D), and 4-Hour (H4) timeframes
Sorted by price descending.

Rules:
  - Level price = close(i) of joint candle
  - Dojis ignored
  - Disrespect counted on closes only
  - Roles: Count 0 = resistance/support, Count 1 = RBS/SBR, Counts 2 & 3 = QM, Count > 3 = RETIRED
  - Retired levels are listed in a separate section.

Supports:
  --asof DATE_TIME : Evaluation cutoff (e.g. "2026-10-02 21:00 UTC" or "2026-06-12")
  --symbol SYMBOL  : Comma-separated symbols (e.g. "AUDCAD,XAGUSD")
  --output FILE    : File to save full report (default: levels_report.txt)
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path
from typing import Optional
import pandas as pd
import pytz
import yaml
from dotenv import load_dotenv

# Force UTF-8 on Windows terminal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import apply_open_continuity, drop_forming, candle_close_time
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import track_key_levels

# Load lookback and strategy settings
SETTINGS_FILE = ROOT / "slk_bot" / "config" / "settings.yaml"
with open(SETTINGS_FILE, encoding="utf-8") as f:
    settings = yaml.safe_load(f)

LOOKBACKS = {
    "W": settings.get("strategy", {}).get("lookback_weekly", 104),
    "D": settings.get("strategy", {}).get("lookback_daily", 250),
    "H4": settings.get("strategy", {}).get("lookback_h4", 300),
}

MAX_DISRESPECTS = settings.get("strategy", {}).get("max_disrespects", 3)

KNOWN_INSTRUMENTS = {
    "EURUSD": {"name": "EURUSD", "symbol": "EUR_USD", "provider": "oanda", "prof": "forex", "dec": 5},
    "XAUUSD": {"name": "XAUUSD", "symbol": "XAU_USD", "provider": "oanda", "prof": "commodity", "dec": 2},
    "XAGUSD": {"name": "XAGUSD", "symbol": "XAG_USD", "provider": "oanda", "prof": "commodity", "dec": 3},
    "NAS100": {"name": "NAS100", "symbol": "NAS100_USD", "provider": "oanda", "prof": "us_index", "dec": 2},
    "BTCUSD": {"name": "BTCUSD", "symbol": "BTCUSDT", "provider": "crypto", "prof": "crypto", "dec": 2},
    "NZDUSD": {"name": "NZDUSD", "symbol": "NZD_USD", "provider": "oanda", "prof": "forex", "dec": 5},
    "NZDJPY": {"name": "NZDJPY", "symbol": "NZD_JPY", "provider": "oanda", "prof": "forex", "dec": 3},
    "AUDCAD": {"name": "AUDCAD", "symbol": "AUD_CAD", "provider": "oanda", "prof": "forex", "dec": 5},
}

DEFAULT_SYMBOLS = ["EURUSD", "XAUUSD", "XAGUSD", "NAS100", "BTCUSD"]


class OutputTee:
    """Tee stdout to both console and a file."""

    def __init__(self, filepath: Path):
        self.file = open(filepath, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, text: str):
        self.stdout.write(text)
        self.file.write(text)

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        self.file.close()


def parse_asof_datetime(asof_str: Optional[str]) -> Optional[pd.Timestamp]:
    """Parse asof string into UTC Timestamp, supporting both date and datetime (with/without UTC)."""
    if not asof_str:
        return None
    # Strip optional 'UTC' or timezone word from end
    cleaned = re.sub(r"\s*UTC\s*$", "", asof_str.strip(), flags=re.IGNORECASE)
    ts = pd.to_datetime(cleaned, utc=True)
    # If time was omitted (only date YYYY-MM-DD), default to Friday 17:00 NY close (21:00 UTC)
    if " " not in cleaned and "T" not in cleaned:
        ts = ts.replace(hour=21, minute=0, second=0)
    return ts


def load_instrument_candles(
    cache: CandleCache,
    inst: dict,
    tf: str,
    lookback: int,
    as_of: Optional[pd.Timestamp],
    oanda: OandaProvider,
    crypto: CryptoProvider,
) -> pd.DataFrame:
    """Load cached candles, dropping forming bars as of cutoff date."""
    name = inst["name"]
    sym = inst["symbol"]
    is_crypto = inst["provider"] == "crypto"
    prof = inst["prof"]

    cached = cache.load_candles(name, tf)
    closed = drop_forming(cached, timeframe_or_duration=tf, alignment=prof, as_of=as_of) if not cached.empty else cached

    if len(closed) < lookback:
        fetch_cnt = max(lookback + 50, 150 if tf == "W" else (350 if tf == "D" else 1500))
        provider = crypto if is_crypto else oanda
        df = provider.get_candles(sym, tf, count=fetch_cnt)
        if prof != "crypto":
            df = apply_open_continuity(df)
        cache.save_candles(name, tf, df)
        closed = drop_forming(df, timeframe_or_duration=tf, alignment=prof, as_of=as_of)

    if len(closed) >= lookback:
        return closed.tail(lookback)
    return closed


def main():
    parser = argparse.ArgumentParser(description="SLK Bias Bot Key Levels Inspector")
    parser.add_argument("--asof", type=str, default=None, help="Evaluation cutoff date/time (e.g. '2026-10-02 21:00 UTC')")
    parser.add_argument("--symbol", type=str, default=None, help="Comma-separated symbols to process (e.g. AUDCAD,XAGUSD)")
    parser.add_argument("--output", type=str, default="levels_report.txt", help="Output file to save full report")
    args = parser.parse_args()

    as_of_ts = parse_asof_datetime(args.asof)
    symbols = [s.strip().upper() for s in args.symbol.split(",")] if args.symbol else DEFAULT_SYMBOLS
    targets = [KNOWN_INSTRUMENTS[s] for s in symbols if s in KNOWN_INSTRUMENTS]

    report_path = ROOT / args.output
    tee = OutputTee(report_path)
    sys.stdout = tee

    try:
        print("=" * 90)
        print(" SLK BIAS BOT - PHASE 1 CHECKPOINT: KEY LEVELS & LEVEL STATES")
        print("=" * 90)
        print("Rules: level price = close(i) | dojis ignored | disrespect on closes only")
        print(f"Roles: A -> RBS -> QM | V -> SBR -> QM | OCL flips once | Retired: count > {MAX_DISRESPECTS}")
        print("Lookback limits: Weekly=104, Daily=250, H4=300")
        if as_of_ts:
            print(f"EVALUATION CUTOFF (--asof): {as_of_ts.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            print("EVALUATION CUTOFF: Current Time (forming bars dropped)")
        print("=" * 90)
        print()

        cache = CandleCache()
        oanda = OandaProvider()
        crypto = CryptoProvider(source="bybit")

        # ----------------------------------------------------------------------
        # PART 1: Last 3 Closed Weekly Candles
        # ----------------------------------------------------------------------
        weekly_targets = [inst for inst in targets if inst["name"] in ("EURUSD", "XAUUSD", "XAGUSD", "NAS100", "BTCUSD", "AUDCAD")]
        if weekly_targets:
            print("------------------------------------------------------------------------------------------")
            print(" PART 1: LAST 3 CLOSED WEEKLY CANDLES (NORMALIZED OHLC)")
            print("------------------------------------------------------------------------------------------")

            for inst in weekly_targets:
                name = inst["name"]
                dec = inst["dec"]
                df_w = load_instrument_candles(cache, inst, "W", 104, as_of_ts, oanda, crypto)
                last3 = df_w.tail(3)

                print(f"\n  • {name} (Weekly - last 3 closed):")
                for ts, row in last3.iterrows():
                    t_str = ts.strftime("%Y-%m-%d %H:%M UTC")
                    print(
                        f"      {t_str} | Open: {row['open']:{dec+4}.{dec}f} | "
                        f"High: {row['high']:{dec+4}.{dec}f} | Low: {row['low']:{dec+4}.{dec}f} | "
                        f"Close: {row['close']:{dec+4}.{dec}f}"
                    )

            print()

        # ----------------------------------------------------------------------
        # PART 2: Key Levels by Instrument & Timeframe (Active vs Retired)
        # ----------------------------------------------------------------------
        print("------------------------------------------------------------------------------------------")
        print(" PART 2: KEY LEVELS BY INSTRUMENT & TIMEFRAME (SORTED BY PRICE)")
        print("------------------------------------------------------------------------------------------")

        for inst in targets:
            name = inst["name"]
            prof = inst["prof"]
            dec = inst["dec"]

            print(f"\n{'='*90}")
            print(f" INSTRUMENT: {name} ({prof.upper()})")
            print(f"{'='*90}")

            for tf in ["W", "D", "H4"]:
                tf_label = "Weekly" if tf == "W" else ("Daily" if tf == "D" else "4-Hour (H4)")
                lookback = LOOKBACKS.get(tf, 250)

                candles = load_instrument_candles(cache, inst, tf, lookback, as_of_ts, oanda, crypto)
                total_candles = len(candles)
                oldest_ts = candles.index[0] if total_candles > 0 else None
                oldest_str = oldest_ts.strftime("%Y-%m-%d %H:%M UTC") if oldest_ts else "None"

                all_levels = track_key_levels(candles, timeframe=tf, symbol=name, lookback=lookback, max_disrespects=MAX_DISRESPECTS)
                
                active_levels = sorted([l for l in all_levels if not l.is_retired], key=lambda l: l.price, reverse=True)
                retired_levels = sorted([l for l in all_levels if l.is_retired], key=lambda l: l.price, reverse=True)

                print(f"\n  [Timeframe: {tf_label}] - Active: {len(active_levels)} | Retired: {len(retired_levels)} | Total: {len(all_levels)} ({lookback} bars):")
                print(f"    Oldest candle used: {oldest_str} | Total candles in lookback: {total_candles}")

                # Print Active Levels
                print(f"\n    --- ACTIVE LEVELS (Disrespect <= {MAX_DISRESPECTS}) ---")
                if not active_levels:
                    print("      (none)")
                else:
                    print(
                        f"      {'Kind':<14} | {'Price':<12} | {'Formed (UTC)':<16} | "
                        f"{'Current Role':<13} | {'Disrespect':<10} | {'Unbroken?':<9} | {'Last Side':<9}"
                    )
                    print(f"      {'-'*14}-+-{'-'*12}-+-{'-'*16}-+-{'-'*13}-+-{'-'*10}-+-{'-'*9}-+-{'-'*9}")

                    for lvl in active_levels:
                        t_str = lvl.formation_time.strftime("%Y-%m-%d %H:%M")
                        p_str = f"{lvl.price:.{dec}f}"
                        unbroken_str = "YES" if lvl.is_unbroken else "NO"
                        print(
                            f"      {lvl.kind.value:<14} | {p_str:<12} | {t_str:<16} | "
                            f"{lvl.current_role.value:<13} | {lvl.disrespect_count:<10} | {unbroken_str:<9} | {lvl.last_side.value:<9}"
                        )

                # Print Retired Levels
                print(f"\n    --- RETIRED LEVELS (Disrespect > {MAX_DISRESPECTS}) ---")
                if not retired_levels:
                    print("      (none)")
                else:
                    print(
                        f"      {'Kind':<14} | {'Price':<12} | {'Formed (UTC)':<16} | "
                        f"{'Current Role':<13} | {'Disrespect':<10} | {'Unbroken?':<9} | {'Last Side':<9}"
                    )
                    print(f"      {'-'*14}-+-{'-'*12}-+-{'-'*16}-+-{'-'*13}-+-{'-'*10}-+-{'-'*9}-+-{'-'*9}")

                    for lvl in retired_levels:
                        t_str = lvl.formation_time.strftime("%Y-%m-%d %H:%M")
                        p_str = f"{lvl.price:.{dec}f}"
                        unbroken_str = "YES" if lvl.is_unbroken else "NO"
                        print(
                            f"      {lvl.kind.value:<14} | {p_str:<12} | {t_str:<16} | "
                            f"{lvl.current_role.value:<13} | {lvl.disrespect_count:<10} | {unbroken_str:<9} | {lvl.last_side.value:<9}"
                        )

        print("\n" + "=" * 90)
        print(" END OF REPORT")
        print("=" * 90)

    finally:
        sys.stdout = tee.stdout
        tee.close()
        print(f"\nFull report successfully saved to {report_path}")


if __name__ == "__main__":
    main()
