#!/usr/bin/env python3
"""
coverage_check.py
-----------------
Phase 0 step 1: instrument coverage report.

What this script does (plain language)
---------------------------------------
1. Connects to your OANDA account and downloads the full list of instruments
   it can trade.
2. Compares that list to every instrument in instruments.yaml.
3. Connects to Binance (or Bybit) and verifies the two crypto symbols exist.
4. Prints a table showing which instruments are AVAILABLE and which are MISSING,
   so you can see at a glance what data you will have.
5. Also prints the last 10 daily, H4 and weekly candles for every available
   instrument (as required by Phase 0 in the brief).

How to run
----------
  python coverage_check.py

You must have your .env file filled in first (OANDA_API_TOKEN, OANDA_ACCOUNT_ID).
"""

from __future__ import annotations

import logging
import io
import os
import sys
from pathlib import Path

# Force UTF-8 output so tick/cross symbols work on Windows terminals
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

# ── Make sure the project root is on the Python path ─────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import resample

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("coverage")

# ── Load instruments ──────────────────────────────────────────────────────
INSTRUMENTS_FILE = ROOT / "slk_bot" / "config" / "instruments.yaml"

with open(INSTRUMENTS_FILE, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)

instruments = cfg["instruments"]

# ── How many recent candles to show ──────────────────────────────────────
SHOW_CANDLES = 10


def fmt_df(df, decimals: int = 5) -> str:
    """Format a candle DataFrame as a readable table."""
    if df.empty:
        return "  (no data)"
    cols = ["open", "high", "low", "close"]
    out = df[cols].tail(SHOW_CANDLES).copy()
    out.index = out.index.strftime("%Y-%m-%d %H:%M UTC")
    return out.to_string(float_format=lambda x: f"{x:.{decimals}f}")


def decimal_places(asset_class: str) -> int:
    return 2 if asset_class in ("crypto", "index", "commodity") else 5


# ==========================================================================
# Step 1 – OANDA coverage
# ==========================================================================
print("\n" + "=" * 70)
print(" STEP 1 – OANDA INSTRUMENT COVERAGE")
print("=" * 70)

try:
    oanda = OandaProvider()
    oanda_symbols = set(oanda.list_symbols())
    log.info("OANDA: %d instruments available on this account.", len(oanda_symbols))
except Exception as exc:
    print(f"\n  ERROR connecting to OANDA: {exc}")
    print("  -> Check your OANDA_API_TOKEN and OANDA_ACCOUNT_ID in .env")
    sys.exit(1)

non_crypto = [i for i in instruments if i["asset_class"] != "crypto"]

available_oanda = []
missing_oanda   = []

for inst in non_crypto:
    sym = inst.get("oanda_symbol")
    if sym and sym in oanda_symbols:
        available_oanda.append(inst)
    else:
        missing_oanda.append(inst)

print(f"\n  Available on OANDA ({len(available_oanda)}):")
for i in available_oanda:
    print(f"    OK  {i['name']:12s}  ->  {i['oanda_symbol']}")

print(f"\n  MISSING from OANDA ({len(missing_oanda)}):")
if not missing_oanda:
    print("    (none – all instruments found)")
else:
    for i in missing_oanda:
        print(f"    MISSING  {i['name']:12s}  ->  tried '{i.get('oanda_symbol', 'N/A')}'")


# ==========================================================================
# Step 2 – Crypto coverage
# ==========================================================================
print("\n" + "=" * 70)
print(" STEP 2 – CRYPTO COVERAGE (Binance)")
print("=" * 70)

crypto_instruments = [i for i in instruments if i["asset_class"] == "crypto"]

try:
    crypto_provider = CryptoProvider(source="binance")
    binance_symbols = set(crypto_provider.list_symbols())
    log.info("Binance: %d symbols available.", len(binance_symbols))
except Exception as exc:
    print(f"\n  WARNING: Binance not reachable ({exc}). Trying Bybit…")
    try:
        crypto_provider = CryptoProvider(source="bybit")
        binance_symbols = set(crypto_provider.list_symbols())
        log.info("Bybit: %d symbols available.", len(binance_symbols))
    except Exception as exc2:
        print(f"  ERROR: Bybit also failed ({exc2}). No crypto data available.")
        binance_symbols = set()

available_crypto = []
missing_crypto   = []

for inst in crypto_instruments:
    sym = inst.get("binance_symbol")
    if sym and sym in binance_symbols:
        available_crypto.append(inst)
    else:
        missing_crypto.append(inst)

print(f"\n  Available ({len(available_crypto)}):")
for i in available_crypto:
    print(f"    OK  {i['name']:10s}  ->  {i.get('binance_symbol','?')} (USDT proxy)")

print(f"\n  MISSING ({len(missing_crypto)}):")
if not missing_crypto:
    print("    (none – all crypto found)")
else:
    for i in missing_crypto:
        print(f"    MISSING  {i['name']}")


# ==========================================================================
# Step 3 – Fetch last 10 candles per available instrument
# ==========================================================================
print("\n" + "=" * 70)
print(" STEP 3 – LAST 10 CANDLES PER INSTRUMENT")
print("=" * 70)
print("  (H1 base → resampled to H4 / Daily / Weekly per session profile)")
print()

errors = []

def fetch_and_show(inst: dict, provider, provider_sym: str, is_crypto: bool) -> None:
    name = inst["name"]
    alignment = inst.get("alignment", "forex")
    dec = decimal_places(inst["asset_class"])

    print(f"  ── {name} ({'crypto' if is_crypto else 'OANDA'}: {provider_sym}) ──")

    try:
        # Fetch enough H1 bars for all three timeframes
        # Weekly: 10 bars × 7 days × 24h = 1680 H1 candles minimum
        h1 = provider.get_candles(provider_sym, "H1", count=2000)

        if h1.empty:
            print(f"    WARNING: no H1 data returned for {name}")
            return

        print(f"    H1 bars fetched: {len(h1):,}")

        for tf, label in [("D", "Daily"), ("H4", "4-Hour"), ("W", "Weekly")]:
            try:
                resampled = resample(h1, tf, alignment)
                print(f"\n    {label} candles (last {SHOW_CANDLES}):")
                print(fmt_df(resampled.tail(SHOW_CANDLES), dec))
            except Exception as e:
                msg = f"    ERROR resampling {name} {tf}: {e}"
                print(msg)
                errors.append(msg)

        print()

    except Exception as e:
        msg = f"  ERROR fetching {name}: {e}"
        print(msg)
        errors.append(msg)


# OANDA instruments
for inst in available_oanda:
    fetch_and_show(inst, oanda, inst["oanda_symbol"], is_crypto=False)

# Crypto
for inst in available_crypto:
    sym = inst.get("binance_symbol", inst.get("bybit_symbol"))
    fetch_and_show(inst, crypto_provider, sym, is_crypto=True)


# ==========================================================================
# Summary
# ==========================================================================
print("\n" + "=" * 70)
print(" COVERAGE SUMMARY")
print("=" * 70)

total = len(instruments)
found = len(available_oanda) + len(available_crypto)
missing_all = missing_oanda + missing_crypto

print(f"\n  Total instruments in brief:  {total}")
print(f"  Data found:                  {found}")
print(f"  Missing:                     {len(missing_all)}")

if missing_all:
    print("\n  Instruments with no data source found:")
    for i in missing_all:
        print(f"    • {i['name']}")
    print(
        "\n  ACTION NEEDED: Review NOTES.md for alternatives, or remove these "
        "from instruments.yaml."
    )
else:
    print("\n  ✓ All instruments have a data source.")

if errors:
    print(f"\n  {len(errors)} error(s) occurred during candle fetching:")
    for e in errors:
        print(f"    {e}")

print(
    "\n  Next step: run calibrate.py to compare these candles with your "
    "TradingView charts before any strategy code is written."
)
print()
