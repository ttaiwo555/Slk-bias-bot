"""
slk_bot/data/providers/oanda.py
--------------------------------
OANDA v20 REST API adapter for forex, indices, commodities, and gold.

Plain-language explanation
--------------------------
This module talks to OANDA on your behalf. It:
  1. Uses your API token from .env to authenticate (never touches any other file).
  2. Lists all the instruments your account can access.
  3. Downloads 1-hour ("H1") candles, which we then resample into H4, Daily,
     and Weekly candles whose boundaries match TradingView (that's done in
     candles.py, not here).
  4. Only returns CLOSED candles — the bar that is still forming right now is
     always dropped, as required by the brief.
  5. Pages through long histories automatically so you get enough lookback.

OANDA environment
-----------------
Set OANDA_ENVIRONMENT=practice in your .env for the paper-trading server,
or =live for your live account. The token must match the environment.

Rate limits
-----------
OANDA does not publish a hard per-second limit for REST candle requests,
but we add a small polite delay between pages to avoid hammering the server.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv

from slk_bot.data.providers.base import DataProvider

load_dotenv()

log = logging.getLogger(__name__)

# OANDA granularity codes we use
_TF_MAP = {
    "H1": "H1",
    "H4": "H4",   # we can also pull H4 natively if calibration allows it
    "D":  "D",
    "W":  "W",
}

_MAX_CANDLES_PER_REQUEST = 5000   # OANDA hard limit per call

# Polite delay between paginated requests (seconds)
_PAGE_DELAY = 0.2


class OandaProvider(DataProvider):
    """
    Adapter for OANDA v20 REST API.

    Reads OANDA_API_TOKEN and OANDA_ACCOUNT_ID from the .env file.
    Set OANDA_ENVIRONMENT to 'practice' or 'live' (default: practice).
    """

    def __init__(self) -> None:
        token = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_API_TOKEN")
        self.account_id = os.getenv("OANDA_ACCOUNT_ID")
        env = os.getenv("OANDA_ENVIRONMENT", "practice").lower()

        if not token:
            raise EnvironmentError(
                "OANDA_API_KEY (or OANDA_API_TOKEN) is not set. "
                "Please add it to your environment variables or GitHub Secrets."
            )
        if not self.account_id:
            raise EnvironmentError(
                "OANDA_ACCOUNT_ID is not set in your .env file. "
                "Please add it (you can find it in the OANDA portal)."
            )

        if env == "live":
            base = "https://api-fxtrade.oanda.com"
        else:
            base = "https://api-fxpractice.oanda.com"
            log.info("Using OANDA PRACTICE environment.")

        self.base_url = base
        self.price_type = "M"   # "M"=mid matches TradingView closest across OHLC; "B"=bid, "A"=ask
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def list_symbols(self) -> list[str]:
        """
        Return every tradable symbol on this OANDA account.
        Used in Phase 0 to find which brief instruments are available.
        """
        url = f"{self.base_url}/v3/accounts/{self.account_id}/instruments"
        resp = self._get(url)
        instruments = resp.get("instruments", [])
        symbols = [i["name"] for i in instruments]
        log.info("OANDA: %d instruments available on this account.", len(symbols))
        return symbols

    def get_candles(
        self,
        symbol: str,
        timeframe: str = "H1",
        since: Optional[pd.Timestamp] = None,
        count: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch closed OHLCV candles from OANDA.

        symbol:    OANDA symbol, e.g. "EUR_USD"
        timeframe: "H1", "H4", "D", or "W"
        since:     fetch candles with open_time >= since (UTC)
        count:     if since is None, fetch this many recent candles

        Returns a DataFrame sorted ascending by open_time.
        The forming (current, open) candle is always excluded.
        """
        gran = _TF_MAP.get(timeframe.upper())
        if gran is None:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}'. "
                f"Supported: {list(_TF_MAP)}"
            )

        if since is not None:
            # Page forward from `since` in chunks of _MAX_CANDLES_PER_REQUEST
            frames = self._fetch_from(symbol, gran, since)
        else:
            # Fetch the most recent `count` candles (one or more pages)
            requested = count or _MAX_CANDLES_PER_REQUEST
            frames = self._fetch_recent(symbol, gran, requested)

        if not frames:
            log.warning("OANDA returned no candles for %s %s.", symbol, timeframe)
            return self._empty_df()

        df = pd.concat(frames, axis=0)
        df = df[~df.index.duplicated(keep="first")]
        df.sort_index(inplace=True)

        # Drop the forming (currently open) candle: it is the last row if its
        # open_time is in the future relative to now.  OANDA already omits it
        # when we set price="M" and omitNonCompleteCandles param, but we make
        # sure here as well.
        now_utc = pd.Timestamp.utcnow()
        df = df[df.index < now_utc]

        return df

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_recent(
        self, symbol: str, gran: str, count: int
    ) -> list[pd.DataFrame]:
        """Fetch the most recent `count` closed candles in reverse pages."""
        url = f"{self.base_url}/v3/instruments/{symbol}/candles"
        frames: list[pd.DataFrame] = []
        remaining = count

        while remaining > 0:
            page_count = min(remaining, _MAX_CANDLES_PER_REQUEST)
            params = {
                "granularity": gran,
                "count": page_count,
                "price": self.price_type,
            }
            if gran in ("H4", "D", "W"):
                params.update({
                    "alignmentTimezone": "America/New_York",
                    "dailyAlignment": 17,
                    "weeklyAlignment": "Sunday",
                })
            data = self._get(url, params=params)
            batch = self._parse_candles(data)
            if batch.empty:
                break
            frames.insert(0, batch)
            remaining -= len(batch)
            if len(batch) < page_count:
                break  # OANDA returned fewer than asked → no more history

        return frames

    def _fetch_from(
        self, symbol: str, gran: str, since: pd.Timestamp
    ) -> list[pd.DataFrame]:
        """
        Page forward from `since`, collecting all candles up to now.
        Each page starts from the last open_time of the previous page + 1 ms.
        """
        url = f"{self.base_url}/v3/instruments/{symbol}/candles"
        frames: list[pd.DataFrame] = []
        from_dt = since

        while True:
            params = {
                "granularity": gran,
                "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "count": _MAX_CANDLES_PER_REQUEST,
                "price": self.price_type,
            }
            if gran in ("H4", "D", "W"):
                params.update({
                    "alignmentTimezone": "America/New_York",
                    "dailyAlignment": 17,
                    "weeklyAlignment": "Sunday",
                })
            data = self._get(url, params=params)
            batch = self._parse_candles(data)
            if batch.empty:
                break

            frames.append(batch)
            last_open = batch.index[-1]

            # Advance the window past the last candle we received
            from_dt = last_open + pd.Timedelta(milliseconds=1)

            if len(batch) < _MAX_CANDLES_PER_REQUEST:
                break  # we've reached the end

            time.sleep(_PAGE_DELAY)

        return frames

    def _parse_candles(self, data: dict) -> pd.DataFrame:
        """Convert raw OANDA candle JSON to a clean DataFrame."""
        raw = data.get("candles", [])
        if not raw:
            return self._empty_df()

        rows = []
        for c in raw:
            if not c.get("complete", True):
                # skip the forming (incomplete) candle
                continue
            # Support bid ("B"), mid ("M"), and ask ("A") price types
            price_data = c.get("bid") or c.get("mid") or c.get("ask") or {}
            rows.append({
                "open_time": pd.Timestamp(c["time"]).tz_convert("UTC"),
                "open":   float(price_data.get("o", "nan")),
                "high":   float(price_data.get("h", "nan")),
                "low":    float(price_data.get("l", "nan")),
                "close":  float(price_data.get("c", "nan")),
                "volume": float(c.get("volume", "nan")),
            })

        if not rows:
            return self._empty_df()

        df = pd.DataFrame(rows)
        df.set_index("open_time", inplace=True)
        df.index = pd.DatetimeIndex(df.index, tz="UTC")
        return df

    def _get(self, url: str, params: Optional[dict] = None) -> dict:
        """Make a GET request with automatic retry on transient connection resets."""
        last_exc = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code in (502, 503, 504):
                    time.sleep(1.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as exc:
                log.error("OANDA HTTP error %s for %s: %s", exc.response.status_code, url, exc)
                raise
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                log.warning("OANDA request attempt %d failed: %s. Retrying...", attempt + 1, exc)
                time.sleep(1.0 * (attempt + 1))
        raise last_exc

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC", name="open_time"),
        )
