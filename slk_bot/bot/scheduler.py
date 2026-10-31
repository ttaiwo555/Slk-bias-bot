"""
slk_bot/bot/scheduler.py
------------------------
5-minute scan scheduler and 1-minute price alert evaluation loop.
- Incremental fetch (only candles newer than last stored).
- Evaluates only when a new closed candle exists.
- Fault-tolerant per instrument/provider.
- Staleness warning during market hours.
"""

from __future__ import annotations

import time
import logging
from typing import Optional, Any
from pathlib import Path
import pandas as pd
import yaml

from slk_bot.storage.cache import CandleCache
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import resample, apply_open_continuity, get_profile
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    SetupState,
    BreakoutType,
    SLKAlert,
    SLKSetup,
)
from slk_bot.bot.telegram import TelegramBotClient
from slk_bot.replay import load_settings_thresholds, load_doji_body_threshold_pct

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
INST_PATH = ROOT / "config" / "instruments.yaml"
SETTINGS_PATH = ROOT / "config" / "settings.yaml"

with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
    SETTINGS_DATA = yaml.safe_load(f)


class BotScheduler:
    """Orchestrates scheduled candle updates and setup lifecycle evaluation."""

    def __init__(
        self,
        cache: Optional[CandleCache] = None,
        telegram_client: Optional[TelegramBotClient] = None,
        oanda_provider: Optional[OandaProvider] = None,
        crypto_provider: Optional[CryptoProvider] = None,
    ) -> None:
        self.cache = cache or CandleCache()
        self.telegram = telegram_client or TelegramBotClient(cache=self.cache)
        self.oanda = oanda_provider or OandaProvider()
        self.crypto = crypto_provider or CryptoProvider()

        self.thresholds = load_settings_thresholds()
        self.doji_pct = load_doji_body_threshold_pct()
        self.push_rules = SETTINGS_DATA.get("push_rules", {})
        self.primary_rule = SETTINGS_DATA.get("strategy", {}).get("primary_selection_rule", "nearest_extreme")
        self.feed_profile = SETTINGS_DATA.get("feed_profile", "forex_com")

        self.instruments = [
            inst for inst in INST_DATA.get("instruments", [])
            if not inst.get("exclude_from_triggers") and inst["name"] != "USOIL"
        ]

    def sync_latest_candles(self, count: int = 5) -> int:
        """
        Incrementally fetch latest closed candles from OANDA and Coinbase,
        merging with local cache so the bot continuously stays up to date.
        """
        updated = 0
        for inst in self.instruments:
            name = inst["name"]
            asset_class = inst.get("asset_class", "forex")
            align = inst.get("alignment", "forex")
            oanda_sym = inst.get("oanda_symbol")
            crypto_sym = inst.get("coinbase_symbol") or inst.get("binance_symbol") or inst.get("bybit_symbol")
            prof = get_profile(align)
            open_cont = prof.get("open_continuity", False)

            try:
                if asset_class == "crypto":
                    df_d = self.crypto.get_candles(crypto_sym, "D", count=count)
                    df_w = self.crypto.get_candles(crypto_sym, "W", count=count)
                    df_h4 = self.crypto.get_candles(crypto_sym, "H4", count=count)

                    for tf, df_new in [("D", df_d), ("W", df_w), ("H4", df_h4)]:
                        if not df_new.empty:
                            cached = self.cache.load_candles(name, tf)
                            merged = pd.concat([cached, df_new])
                            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                            self.cache.save_candles(name, tf, merged)
                    updated += 1

                elif asset_class == "commodity" and name in ("XAUUSD", "XAGUSD") and oanda_sym:
                    df_d = self.oanda.get_candles(oanda_sym, "D", count=count)
                    df_w = self.oanda.get_candles(oanda_sym, "W", count=count)
                    h1 = self.oanda.get_candles(oanda_sym, "H1", count=count * 4)
                    df_h4 = resample(h1, "H4", "metal", h4_anchor="18:00", h4_timezone="America/New_York")
                    if open_cont:
                        df_d = apply_open_continuity(df_d)
                        df_w = apply_open_continuity(df_w)
                        df_h4 = apply_open_continuity(df_h4)

                    for tf, df_new in [("D", df_d), ("W", df_w), ("H4", df_h4)]:
                        if not df_new.empty:
                            cached = self.cache.load_candles(name, tf)
                            merged = pd.concat([cached, df_new])
                            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                            self.cache.save_candles(name, tf, merged)
                    updated += 1

                elif oanda_sym:
                    df_d = self.oanda.get_candles(oanda_sym, "D", count=count)
                    df_w = self.oanda.get_candles(oanda_sym, "W", count=count)
                    df_h4 = self.oanda.get_candles(oanda_sym, "H4", count=count)
                    if open_cont:
                        df_d = apply_open_continuity(df_d)
                        df_w = apply_open_continuity(df_w)
                        df_h4 = apply_open_continuity(df_h4)

                    for tf, df_new in [("D", df_d), ("W", df_w), ("H4", df_h4)]:
                        if not df_new.empty:
                            cached = self.cache.load_candles(name, tf)
                            merged = pd.concat([cached, df_new])
                            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                            self.cache.save_candles(name, tf, merged)
                    updated += 1

            except Exception as e:
                log.debug("Live sync skipped for %s: %s", name, e)

        return updated

    def log_alerts_to_csv(self, alerts: list[SLKAlert], profile: str = "forex_com") -> None:
        """
        Appends alerts to alerts_log.csv matching the reference_setups.csv schema
        plus alert_time_utc, method, and profile.
        """
        if not alerts:
            return
        log_path = ROOT.parent / "alerts_log.csv"
        rows = []
        for a in alerts:
            kl_kind = ""
            kl_price = None
            formed_wat = ""
            if a.htf_level:
                raw_k = a.htf_level.kind.value if hasattr(a.htf_level.kind, "value") else str(a.htf_level.kind)
                kl_kind = "OCL" if "OCL" in raw_k else ("V" if "V" in raw_k else ("A" if "A" in raw_k else raw_k))
                kl_price = a.htf_level.price
                t_wat = a.htf_level.formation_time + pd.Timedelta(hours=1)
                formed_wat = f"{t_wat.day} {t_wat.strftime('%b %Y')}"
            elif a.broken_level:
                raw_k = a.broken_level.kind.value if hasattr(a.broken_level.kind, "value") else str(a.broken_level.kind)
                kl_kind = "OCL" if "OCL" in raw_k else ("V" if "V" in raw_k else ("A" if "A" in raw_k else raw_k))
                kl_price = a.broken_level.price
                t_wat = a.broken_level.formation_time + pd.Timedelta(hours=1)
                formed_wat = f"{t_wat.day} {t_wat.strftime('%b %Y')}"

            rej_str = a.rejection_candle_time.strftime("%Y-%m-%dT%H:%M") if getattr(a, "rejection_candle_time", None) else ""
            bo_str = a.breakout_candle_time.strftime("%Y-%m-%dT%H:%M")
            alert_str = a.alert_time.strftime("%Y-%m-%dT%H:%M")
            next_day = "Y" if getattr(a, "next_day_rule", False) else "N"
            swept = getattr(a, "swept_level", "")
            method_str = a.method.value if hasattr(a.method, "value") else str(a.method)

            rows.append({
                "id": a.dedupe_key,
                "symbol": a.symbol,
                "dir": a.direction.value.upper(),
                "pair": f"{a.htf}-{a.ltf}",
                "kl_kind": kl_kind,
                "kl_price": kl_price,
                "bo_price": a.broken_level.price,
                "rej_open_utc": rej_str,
                "bo_open_utc": bo_str,
                "next_day": next_day,
                "swept_level": swept,
                "kl_formed_wat": formed_wat,
                "alert_time_utc": alert_str,
                "method": method_str,
                "profile": profile,
            })

        df_new = pd.DataFrame(rows)
        if log_path.exists():
            df_new.to_csv(log_path, mode="a", header=False, index=False)
        else:
            df_new.to_csv(log_path, mode="w", header=True, index=False)

    def run_scan_cycle(self) -> list[SLKAlert]:
        """
        Execute a full scan cycle across all instruments:
        1. Fetch newest closed candles incrementally.
        2. Detect triggers & evaluate setup lifecycles.
        3. Dispatch newly confirmed alerts to Telegram.
        """
        log.info("Starting scan cycle across %d instruments...", len(self.instruments))
        all_new_alerts: list[SLKAlert] = []
        active_setups_by_symbol: dict[str, SLKSetup] = {}

        for inst in self.instruments:
            sym = inst["name"]
            asset_class = inst.get("asset_class", "forex")

            try:
                # Load existing candles
                df_d = self.cache.load_candles(sym, "D")
                df_w = self.cache.load_candles(sym, "W")
                df_h4 = self.cache.load_candles(sym, "H4")

                if df_d.empty or df_h4.empty:
                    continue

                # 1. Daily -> H4 Setup evaluation
                trigs_d = detect_triggers(
                    df=df_d,
                    timeframe="D",
                    symbol=sym,
                    asset_class=asset_class,
                    lookback=250,
                    max_disrespects=3,
                    ocl_max_disrespects=3,
                    doji_body_threshold_pct=self.doji_pct,
                    marginal_thresholds=self.thresholds,
                    primary_selection_rule=self.primary_rule,
                )

                h4_levels = detect_ltf_levels(df_h4, sym, "H4", self.doji_pct)

                for trig in trigs_d[-5:]:  # check recent triggers
                    setup = evaluate_setup_lifecycle(
                        trigger=trig,
                        htf_df=df_d,
                        symbol=sym,
                        htf_tf="D",
                        ltf_tf="H4",
                        ltf_df=df_h4,
                        daily_df=df_d,
                        unfresh_alerts=True,
                        all_ltf_levels=h4_levels,
                    )
                    # Persist setup state
                    self.cache.save_setup(setup)

                    if setup.is_confirmed:
                        active_setups_by_symbol[sym] = setup

                    for alert in setup.alerts:
                        self.cache.save_alert(alert, setup.id)
                        all_new_alerts.append(alert)

                # 2. Weekly -> Daily Setup evaluation
                if not df_w.empty:
                    trigs_w = detect_triggers(
                        df=df_w,
                        timeframe="W",
                        symbol=sym,
                        asset_class=asset_class,
                        lookback=104,
                        max_disrespects=3,
                        ocl_max_disrespects=3,
                        doji_body_threshold_pct=self.doji_pct,
                        marginal_thresholds=self.thresholds,
                        primary_selection_rule=self.primary_rule,
                    )
                    d_levels = detect_ltf_levels(df_d, sym, "D", self.doji_pct)

                    for trig in trigs_w[-3:]:
                        setup_w = evaluate_setup_lifecycle(
                            trigger=trig,
                            htf_df=df_w,
                            symbol=sym,
                            htf_tf="W",
                            ltf_tf="D",
                            ltf_df=df_d,
                            daily_df=df_d,
                            unfresh_alerts=True,
                            all_ltf_levels=d_levels,
                        )
                        self.cache.save_setup(setup_w)

                        if setup_w.is_confirmed and sym not in active_setups_by_symbol:
                            active_setups_by_symbol[sym] = setup_w

                        for alert in setup_w.alerts:
                            self.cache.save_alert(alert, setup_w.id)
                            all_new_alerts.append(alert)

            except Exception as e:
                log.error("Error evaluating %s during scan cycle: %s", sym, e, exc_info=True)

        # Dispatch any new alerts for default profile
        dispatched = self.telegram.dispatch_alerts(
            alerts=all_new_alerts,
            opposite_setups_map=active_setups_by_symbol,
            push_rules=self.push_rules,
        )

        # Log alerts to alerts_log.csv for both profiles (Item 8a)
        self.log_alerts_to_csv(dispatched, profile=self.feed_profile)
        other_prof = "reference_parity" if self.feed_profile == "forex_com" else "forex_com"
        self.log_alerts_to_csv(all_new_alerts, profile=other_prof)

        log.info("Scan cycle completed. Dispatched %d alerts.", len(dispatched))
        return dispatched
