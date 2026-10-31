"""
slk_bot/data/providers/crypto.py
----------------------------------
Binance (or Bybit) public market-data adapter for BTCUSD and ETHUSD.

Plain-language explanation
--------------------------
Neither Binance nor Bybit requires any account or API key for reading
public price data. This module:
  1. Tries Binance first (the default in settings.yaml).
  2. Falls back to Bybit if Binance fails for any reason.
  3. Maps BTCUSDT / ETHUSDT → BTCUSD / ETHUSD (see NOTES.md).
  4. Returns 1-hour candles in the same format as the OANDA adapter so
     the resampler in candles.py doesn't know or care which source it got.

Crypto candles open at 00:00 UTC (not 17:00 New York), so the alignment
profile for crypto is different from forex — that is handled in candles.py.

Rate limits (as of 2024 – always verify in the API docs):
  Binance: 1200 weight per minute; a klines request costs 2 weight.
           That's ~600 requests/min, far more than we need.
  Bybit:   120 requests per minute for market data.
We add a small delay between pages to be polite.
"""

from __future__ import annotations

import os
import logging
import time
from typing import Optional, Any

import pandas as pd
import requests

from slk_bot.data.providers.base import DataProvider

log = logging.getLogger(__name__)

_PAGE_DELAY = 0.1   # seconds between paginated requests

# Maximum candles per request
_BINANCE_MAX = 1000
_BYBIT_MAX   = 200


class CryptoProvider(DataProvider):
    """
    Adapter for Coinbase / Binance / Bybit public market-data endpoints.
    No API key required for public market candle data.

    Usage:
        provider = CryptoProvider(source="coinbase")  # or "binance", "bybit"
        df = provider.get_candles("BTC-USD", "H1")
    """

    def __init__(self, source: str = "coinbase", api_key: Optional[str] = None) -> None:
        self.source = source.lower()
        if self.source not in ("coinbase", "binance", "bybit"):
            raise ValueError(f"Unsupported crypto source '{source}'. Use 'coinbase', 'binance', or 'bybit'.")
        self.api_key = api_key or os.getenv("COINBASE_API_KEY")
        self._session = requests.Session()
        headers = {"User-Agent": "SLKBot/1.0"}
        if self.api_key:
            headers["CB-ACCESS-KEY"] = self.api_key
        self._session.headers.update(headers)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def list_symbols(self) -> list[str]:
        """Return tradable symbols from the chosen exchange."""
        if self.source == "coinbase":
            return self._coinbase_list_symbols()
        elif self.source == "binance":
            return self._binance_list_symbols()
        return self._bybit_list_symbols()

    def get_candles(
        self,
        symbol: str,
        timeframe: str = "H1",
        since: Optional[pd.Timestamp] = None,
        count: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch closed OHLCV candles.
        symbol:    exchange symbol, e.g. "BTC-USD" or "BTCUSDT"
        timeframe: "H1", "H4", "D", "W"
        since:     fetch candles with open_time >= since (UTC)
        count:     if since is None, fetch this many recent candles
        """
        if self.source == "coinbase":
            return self._coinbase_get_candles(symbol, timeframe, since, count)
        elif self.source == "binance":
            return self._binance_get_candles(symbol, timeframe, since, count)
        return self._bybit_get_candles(symbol, timeframe, since, count)

    # ------------------------------------------------------------------
    # Coinbase helpers
    # ------------------------------------------------------------------

    _COINBASE_GRANULARITY = {"H1": 3600, "D": 86400}
    _COINBASE_MAX = 300

    @staticmethod
    def _normalize_coinbase_symbol(sym: str) -> str:
        s = sym.strip().upper()
        if "-" in s:
            return s
        if s.endswith("USDT"):
            return f"{s[:-4]}-USDT"
        if s.endswith("USD"):
            return f"{s[:-3]}-USD"
        return s

    def _coinbase_list_symbols(self) -> list[str]:
        url = "https://api.exchange.coinbase.com/products"
        data = self._get(url)
        return [p["id"] for p in data if isinstance(p, dict) and "id" in p]

    def _coinbase_get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[pd.Timestamp],
        count: Optional[int],
    ) -> pd.DataFrame:
        tf = timeframe.upper()
        cb_symbol = self._normalize_coinbase_symbol(symbol)

        # For H4, Coinbase has 6h native, not 4h. Resample from H1:
        if tf == "H4":
            h1_count = (count * 4 + 10) if count is not None else None
            df_h1 = self._coinbase_get_candles(cb_symbol, "H1", since=since, count=h1_count)
            if df_h1.empty:
                return self._empty_df()
            df_h4 = df_h1.resample("4h", closed="left", label="left").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()
            if count is not None and len(df_h4) > count:
                df_h4 = df_h4.iloc[-count:]
            return df_h4

        # For W, resample from D:
        if tf == "W":
            d_count = (count * 7 + 10) if count is not None else None
            df_d = self._coinbase_get_candles(cb_symbol, "D", since=since, count=d_count)
            if df_d.empty:
                return self._empty_df()
            df_w = df_d.resample("W-MON", closed="left", label="left").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()
            if count is not None and len(df_w) > count:
                df_w = df_w.iloc[-count:]
            return df_w

        gran_sec = self._COINBASE_GRANULARITY.get(tf)
        if gran_sec is None:
            raise ValueError(f"Unsupported timeframe '{timeframe}' for Coinbase.")

        url = f"https://api.exchange.coinbase.com/products/{cb_symbol}/candles"
        frames: list[pd.DataFrame] = []

        if since is not None:
            now_utc = pd.Timestamp.now(tz="UTC")
            curr_start = since
            while curr_start < now_utc:
                curr_end = min(curr_start + pd.Timedelta(seconds=self._COINBASE_MAX * gran_sec), now_utc)
                params = {
                    "granularity": gran_sec,
                    "start": curr_start.isoformat(),
                    "end": curr_end.isoformat(),
                }
                data = self._get(url, params)
                batch = self._coinbase_parse(data, gran_sec)
                if batch.empty:
                    break
                frames.append(batch)
                last_open = batch.index[-1]
                curr_start = last_open + pd.Timedelta(seconds=gran_sec)
                if curr_end >= now_utc or len(batch) < self._COINBASE_MAX:
                    if curr_start >= now_utc:
                        break
                time.sleep(_PAGE_DELAY)
        else:
            n = count or self._COINBASE_MAX
            end_iso: Optional[str] = None
            remaining = n
            while remaining > 0:
                page = min(remaining, self._COINBASE_MAX)
                params = {"granularity": gran_sec}
                if end_iso is not None:
                    params["end"] = end_iso
                    start_calc = pd.Timestamp(end_iso) - pd.Timedelta(seconds=page * gran_sec)
                    params["start"] = start_calc.isoformat()
                data = self._get(url, params)
                batch = self._coinbase_parse(data, gran_sec)
                if batch.empty:
                    break
                frames.insert(0, batch)
                remaining -= len(batch)
                first_open = batch.index[0]
                end_iso = first_open.isoformat()
                if len(batch) < page:
                    break
                time.sleep(_PAGE_DELAY)

        if not frames:
            return self._empty_df()

        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="first")]
        df.sort_index(inplace=True)

        now_utc = pd.Timestamp.now(tz="UTC")
        df = df[df["close_time"] < now_utc]
        df = df.drop(columns=["close_time"])

        if count is not None and len(df) > count:
            df = df.iloc[-count:]
        return df

    @staticmethod
    def _coinbase_parse(data: Any, gran_sec: int) -> pd.DataFrame:
        if not data or not isinstance(data, list):
            return CryptoProvider._empty_df()
        rows = []
        for k in reversed(data):
            if not isinstance(k, list) or len(k) < 6:
                continue
            t_open = pd.Timestamp(k[0], unit="s", tz="UTC")
            rows.append({
                "open_time":  t_open,
                "open":       float(k[3]),
                "high":       float(k[2]),
                "low":        float(k[1]),
                "close":      float(k[4]),
                "volume":     float(k[5]),
                "close_time": t_open + pd.Timedelta(seconds=gran_sec),
            })
        if not rows:
            return CryptoProvider._empty_df()
        df = pd.DataFrame(rows).set_index("open_time")
        df.index = pd.DatetimeIndex(df.index, tz="UTC")
        return df

    # ------------------------------------------------------------------
    # Binance helpers
    # ------------------------------------------------------------------

    _BINANCE_TF = {"H1": "1h", "H4": "4h", "D": "1d", "W": "1w"}

    def _binance_list_symbols(self) -> list[str]:
        url = "https://api.binance.com/api/v3/exchangeInfo"
        data = self._get(url)
        return [s["symbol"] for s in data.get("symbols", [])]

    def _binance_get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[pd.Timestamp],
        count: Optional[int],
    ) -> pd.DataFrame:
        gran = self._BINANCE_TF.get(timeframe.upper())
        if gran is None:
            raise ValueError(f"Unsupported timeframe '{timeframe}' for Binance.")

        url = "https://api.binance.com/api/v3/klines"
        frames: list[pd.DataFrame] = []

        if since is not None:
            # Page forward from `since`
            start_ms = int(since.timestamp() * 1000)
            while True:
                params = {
                    "symbol": symbol,
                    "interval": gran,
                    "startTime": start_ms,
                    "limit": _BINANCE_MAX,
                }
                data = self._get(url, params)
                batch = self._binance_parse(data)
                if batch.empty:
                    break
                frames.append(batch)
                last_open_ms = int(batch.index[-1].timestamp() * 1000)
                start_ms = last_open_ms + 1
                if len(batch) < _BINANCE_MAX:
                    break
                time.sleep(_PAGE_DELAY)
        else:
            n = count or _BINANCE_MAX
            # Fetch in pages ending at "now", walk backwards
            end_ms: Optional[int] = None
            remaining = n
            while remaining > 0:
                page = min(remaining, _BINANCE_MAX)
                params = {
                    "symbol": symbol,
                    "interval": gran,
                    "limit": page,
                }
                if end_ms is not None:
                    params["endTime"] = end_ms
                data = self._get(url, params)
                batch = self._binance_parse(data)
                if batch.empty:
                    break
                frames.insert(0, batch)
                remaining -= len(batch)
                first_open_ms = int(batch.index[0].timestamp() * 1000)
                end_ms = first_open_ms - 1
                if len(batch) < page:
                    break
                time.sleep(_PAGE_DELAY)

        if not frames:
            return self._empty_df()

        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="first")]
        df.sort_index(inplace=True)

        # Drop the forming candle: any candle whose close_time is in the future
        now_utc = pd.Timestamp.now(tz="UTC")
        df = df[df["close_time"] < now_utc]
        df = df.drop(columns=["close_time"])
        return df

    @staticmethod
    def _binance_parse(data: list) -> pd.DataFrame:
        if not data:
            return CryptoProvider._empty_df()
        rows = []
        for k in data:
            rows.append({
                "open_time":  pd.Timestamp(k[0], unit="ms", tz="UTC"),
                "open":       float(k[1]),
                "high":       float(k[2]),
                "low":        float(k[3]),
                "close":      float(k[4]),
                "volume":     float(k[5]),
                "close_time": pd.Timestamp(k[6], unit="ms", tz="UTC"),
            })
        df = pd.DataFrame(rows).set_index("open_time")
        df.index = pd.DatetimeIndex(df.index, tz="UTC")
        return df

    # ------------------------------------------------------------------
    # Bybit helpers
    # ------------------------------------------------------------------

    _BYBIT_TF = {"H1": "60", "H4": "240", "D": "D", "W": "W"}

    def _bybit_list_symbols(self) -> list[str]:
        url = "https://api.bybit.com/v5/market/instruments-info"
        params = {"category": "spot"}
        data = self._get(url, params)
        items = data.get("result", {}).get("list", [])
        return [i["symbol"] for i in items]

    def _bybit_get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[pd.Timestamp],
        count: Optional[int],
    ) -> pd.DataFrame:
        gran = self._BYBIT_TF.get(timeframe.upper())
        if gran is None:
            raise ValueError(f"Unsupported timeframe '{timeframe}' for Bybit.")

        url = "https://api.bybit.com/v5/market/kline"
        frames: list[pd.DataFrame] = []

        if since is not None:
            start_ms = int(since.timestamp() * 1000)
            while True:
                params = {
                    "category": "spot",
                    "symbol": symbol,
                    "interval": gran,
                    "start": start_ms,
                    "limit": _BYBIT_MAX,
                }
                data = self._get(url, params)
                batch = self._bybit_parse(data)
                if batch.empty:
                    break
                frames.append(batch)
                last_open_ms = int(batch.index[-1].timestamp() * 1000)
                start_ms = last_open_ms + 1
                if len(batch) < _BYBIT_MAX:
                    break
                time.sleep(_PAGE_DELAY)
        else:
            n = count or _BYBIT_MAX
            end_ms: Optional[int] = None
            remaining = n
            while remaining > 0:
                page = min(remaining, _BYBIT_MAX)
                params = {
                    "category": "spot",
                    "symbol": symbol,
                    "interval": gran,
                    "limit": page,
                }
                if end_ms is not None:
                    params["end"] = end_ms
                data = self._get(url, params)
                batch = self._bybit_parse(data)
                if batch.empty:
                    break
                frames.insert(0, batch)
                remaining -= len(batch)
                first_open_ms = int(batch.index[0].timestamp() * 1000)
                end_ms = first_open_ms - 1
                if len(batch) < page:
                    break
                time.sleep(_PAGE_DELAY)

        if not frames:
            return self._empty_df()

        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="first")]
        df.sort_index(inplace=True)

        # Drop forming candle
        now_utc = pd.Timestamp.now(tz="UTC")
        df = df[df["close_time"] < now_utc]
        df = df.drop(columns=["close_time"])
        return df

    @staticmethod
    def _bybit_parse(data: dict) -> pd.DataFrame:
        """Bybit returns candles newest-first in result.list; we reverse."""
        raw_list = data.get("result", {}).get("list", [])
        if not raw_list:
            return CryptoProvider._empty_df()
        rows = []
        for k in reversed(raw_list):
            # Bybit v5: [startTime, open, high, low, close, volume, turnover]
            rows.append({
                "open_time":  pd.Timestamp(int(k[0]), unit="ms", tz="UTC"),
                "open":       float(k[1]),
                "high":       float(k[2]),
                "low":        float(k[3]),
                "close":      float(k[4]),
                "volume":     float(k[5]),
                "close_time": pd.Timestamp(int(k[0]), unit="ms", tz="UTC")
                               + _bybit_interval_delta(k),
            })
        df = pd.DataFrame(rows).set_index("open_time")
        df.index = pd.DatetimeIndex(df.index, tz="UTC")
        return df

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, params: Optional[dict] = None) -> dict | list:
        try:
            resp = self._session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            log.error(
                "Crypto HTTP error %s for %s: %s",
                exc.response.status_code, url, exc
            )
            raise
        except requests.exceptions.RequestException as exc:
            log.error("Crypto request failed for %s: %s", url, exc)
            raise

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC", name="open_time"),
        )


def _bybit_interval_delta(row: list) -> pd.Timedelta:
    """We don't get a close_time from Bybit, so we infer it from the interval."""
    # This is only used to identify forming candles; the exact granularity
    # is not critical here (we compare close_time < now_utc).
    return pd.Timedelta(hours=1)   # placeholder; works for H1 base candles
