"""
slk_bot/bot/handlers.py
-----------------------
Telegram command handlers and message processing logic for run-once mode.
Access Control: Only TELEGRAM_CHAT_ID may interact with the bot; all other chat IDs are dropped.
Price alerts (/pricealert, /pricealerts, /cancelalert) have been removed.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Optional, Any
from pathlib import Path
import pandas as pd
import yaml

from slk_bot.storage.cache import CandleCache
from slk_bot.bot.formatter import (
    format_price,
    format_alert_message,
    format_internal_setup_message,
    get_display_scale,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
INST_PATH = ROOT / "config" / "instruments.yaml"
SETTINGS_PATH = ROOT / "config" / "settings.yaml"

with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
    SETTINGS_DATA = yaml.safe_load(f)


class BotCommandHandler:
    """Processes incoming Telegram commands with strict authorization."""

    def __init__(
        self,
        owner_chat_id: Optional[str] = None,
        cache: Optional[CandleCache] = None,
        dry_run: bool = False,
    ) -> None:
        self.owner_chat_id = str(owner_chat_id or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_OWNER_CHAT_ID", "")).strip()
        self.cache = cache or CandleCache()
        self.dry_run = dry_run
        self.instruments = {
            inst["name"]: inst for inst in INST_DATA.get("instruments", [])
            if not inst.get("exclude_from_triggers")
        }
        self.alert_preferences = dict(SETTINGS_DATA.get("alert_preferences", {
            "external_bo": True,
            "internal_bo": False,
            "trend_alignment": "required",
        }))

    def is_authorized(self, chat_id: str | int) -> bool:
        """Only the configured owner chat ID is authorized."""
        cid_str = str(chat_id).strip()
        if cid_str == "console":
            return True
        if not self.owner_chat_id:
            return self.dry_run
        return cid_str == self.owner_chat_id

    def handle_message(self, chat_id: str | int, text: str) -> Optional[str]:
        """
        Main entrypoint for incoming messages.
        Returns a response string, or None if dropped (unauthorized).
        """
        if not self.is_authorized(chat_id):
            log.warning("Dropping message from unauthorized chat_id: %s", chat_id)
            return None

        text = text.strip()
        if not text.startswith("/"):
            return "Please use a valid command. Type /help to see available commands."

        parts = text.split()
        cmd = parts[0].lower().split("@")[0]  # strip @botname
        args = parts[1:]

        if cmd in ("/start",):
            return self._cmd_start()
        elif cmd in ("/help",):
            return self._cmd_help()
        elif cmd in ("/bias",):
            return self._cmd_bias(args)
        elif cmd in ("/active",):
            return self._cmd_active()
        elif cmd in ("/watching",):
            return self._cmd_watching()
        elif cmd in ("/lowprob",):
            return self._cmd_lowprob()
        elif cmd in ("/internal",):
            return self._cmd_internal()
        elif cmd in ("/scan",):
            return self._cmd_scan()
        elif cmd in ("/status",):
            return self._cmd_status()
        elif cmd in ("/instruments",):
            return self._cmd_instruments()
        elif cmd in ("/verify",):
            return self._cmd_verify(args)
        elif cmd in ("/alerts",):
            return self._cmd_alerts(args)
        elif cmd in ("/terms",):
            return self._cmd_terms()
        else:
            return f"Unknown command <code>{cmd}</code>. Type /help for assistance."

    def _cmd_start(self) -> str:
        return (
            "👋 <b>Welcome to SLK Bias Bot!</b>\n\n"
            "The bot monitors HTF key levels, liquidity sweeps, and LTF structural breakouts "
            "across 37 watchlist instruments.\n\n"
            "<b>Quick Commands:</b>\n"
            "• /active – View currently active confirmed setups\n"
            "• /internal – View active internal breakouts\n"
            "• /watching – View setups currently waiting for LTF breakout\n"
            "• /alerts – View or change alert preferences (External/Internal/Trend)\n"
            "• /terms – Plain language explanation of alert terms\n"
            "• /status – Check bot run time, new candle times, and database stats\n"
            "• /help – Full list of commands\n"
        )

    def _cmd_help(self) -> str:
        return (
            "📖 <b>SLK Bias Bot Command Reference</b>\n\n"
            "<b>Strategy & Setups:</b>\n"
            "• /bias <code>SYMBOL</code> – Display current HTF bias and key levels\n"
            "• /active – List all active confirmed setups\n"
            "• /internal – List setups with active internal breakouts awaiting external\n"
            "• /watching – List setups waiting for LTF confirmation\n"
            "• /lowprob – List low probability triggers & unfresh Method 1 setups\n"
            "• /scan – Trigger an immediate scan across all instruments\n"
            "• /terms – Plain language guide explaining all alert terminology\n\n"
            "<b>Alert Preferences:</b>\n"
            "• /alerts – View and change alert preferences (External BO, Internal BO, Trend alignment)\n\n"
            "<b>System & Verification:</b>\n"
            "• /status – View last run time, candle close times, state age, and stats\n"
            "• /instruments – List all watched instruments and settings\n"
            "• /verify <code>SYMBOL TIMEFRAME</code> – Inspect candle timestamps and active levels\n"
        )

    def _cmd_bias(self, args: list[str]) -> str:
        if not args:
            return "Usage: /bias <code>SYMBOL</code> (e.g. <code>/bias EURUSD</code>)"
        sym = args[0].upper()
        if sym not in self.instruments:
            return f"Symbol <code>{sym}</code> is not in the active watchlist. Type /instruments to view list."

        df_d = self.cache.load_candles(sym, "D")
        if df_d.empty:
            return f"No cached candle data found for <code>{sym}</code>."

        last_close = float(df_d["close"].iloc[-1])
        last_time = df_d.index[-1].strftime("%Y-%m-%d %H:%M UTC")

        # Check latest setups for this symbol
        setups = self.cache.get_setups(sym)
        active_setups = [s for s in setups if s.get("state") in ("EXTERNAL_CONFIRMED", "INTERNAL_CONFIRMED")]

        lines = [
            f"📊 <b>HTF Bias Analysis: {sym}</b>",
            f"• <b>Latest Daily Close:</b> {format_price(last_close, sym)} ({last_time})",
        ]

        if active_setups:
            lines.append("\n<b>Active Confirmed Setups:</b>")
            for s in active_setups:
                d_str = s['direction'].upper()
                emoji = "🟢" if s['direction'].lower() == "bullish" else "🔴"
                htf = "Daily" if s['htf'] == "D" else "Weekly"
                state = s['state']
                lines.append(f"• {emoji} <b>{d_str}</b> on {htf} – State: <code>{state}</code>")
        else:
            lines.append("\n<b>Bias Status:</b> NEUTRAL (no active confirmed breakouts)")

        return "\n".join(lines)

    def _cmd_active(self) -> str:
        setups = self.cache.get_setups()
        active = [s for s in setups if s.get("state") in ("EXTERNAL_CONFIRMED", "INTERNAL_CONFIRMED")]
        if not active:
            return "No currently active confirmed setups."

        lines = [f"🟢 <b>Active Confirmed Setups ({len(active)}):</b>\n"]
        for s in active:
            sym = s["symbol"]
            d_str = s["direction"].upper()
            emoji = "🟢" if s["direction"].lower() == "bullish" else "🔴"
            htf = "Daily" if s["htf"] == "D" else "Weekly"
            state = s["state"]
            x_price = format_price(float(s["extreme_x"]), sym)
            lines.append(f"• {emoji} <b>{sym}</b> – {d_str} ({htf}) | {state} | X: <code>{x_price}</code>")

        return "\n".join(lines)

    def _cmd_watching(self) -> str:
        setups = self.cache.get_setups()
        watching = [s for s in setups if s.get("state") == "WATCHING"]
        if not watching:
            return "No setups currently in WATCHING state."

        lines = [f"👀 <b>Watching Setups ({len(watching)} awaiting LTF breakout):</b>\n"]
        for s in watching[:25]:
            sym = s["symbol"]
            d_str = s["direction"].upper()
            emoji = "🟢" if s["direction"].lower() == "bullish" else "🔴"
            htf = "Daily" if s["htf"] == "D" else "Weekly"
            x_price = format_price(float(s["extreme_x"]), sym)
            lines.append(f"• {emoji} <b>{sym}</b> – {d_str} ({htf}) | X: <code>{x_price}</code>")

        if len(watching) > 25:
            lines.append(f"\n<i>...and {len(watching) - 25} more.</i>")

        return "\n".join(lines)

    def _cmd_lowprob(self) -> str:
        alerts = self.cache.get_alerts()
        lowprob = [a for a in alerts if a.get("is_low_prob")]
        if not lowprob:
            return "No low probability / unfresh triggers currently recorded."

        lines = [f"⚠️ <b>Low Probability Triggers & Unfresh Setups ({len(lowprob)}):</b>\n"]
        for a in lowprob[-20:]:
            sym = a["symbol"]
            d_str = a["direction"].upper()
            htf = "Daily" if a["htf"] == "D" else "Weekly"
            b_type = a["breakout_type"]
            time_str = a["alert_time"]
            lines.append(f"• <b>{sym}</b> {d_str} ({htf}) – {b_type} on {time_str} [LOW PROB]")

        return "\n".join(lines)

    def _cmd_internal(self) -> str:
        setups = self.cache.get_setups()
        internal_setups = [s for s in setups if s.get("state") == "INTERNAL_CONFIRMED"]

        if not internal_setups:
            return "No setups currently have an active internal breakout awaiting external confirmation."

        lines = [f"⚡ <b>Active Internal Breakouts ({len(internal_setups)}):</b>\n"]
        for s in internal_setups:
            sym = s["symbol"]
            d_str = s["direction"].upper()
            emoji = "🟢" if s["direction"].lower() == "bullish" else "🔴"
            htf = "Daily" if s["htf"] == "D" else "Weekly"
            ltf = "H4" if s["ltf"] == "H4" else "Daily"
            x_price = format_price(float(s["extreme_x"]), sym)
            lines.append(
                f"• {emoji} <b>{sym}</b> {d_str} ({htf} $\to$ {ltf})\n"
                f"   Mitigation Extreme (X): <code>{x_price}</code> | Status: <i>Awaiting LX break</i>"
            )

        return "\n".join(lines)

    def _cmd_scan(self) -> str:
        return "🔄 <b>Scan initiated.</b> A complete scan across all 37 watchlist instruments will run immediately on this execution cycle."

    def _cmd_status(self) -> str:
        # Query database stats
        with self.cache._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM candles")
            total_candles = c.fetchone()[0]
            c.execute("SELECT count(*) FROM setups")
            total_setups = c.fetchone()[0]
            c.execute("SELECT count(*) FROM alerts_sent")
            total_alerts_sent = c.fetchone()[0]

        now_utc = pd.Timestamp.now("UTC")
        now_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        dry_str = "ENABLED (Console Only)" if self.dry_run else "DISABLED (Live Telegram)"

        # Read state info from state.json
        state_info: dict[str, Any] = {}
        for candidate in [ROOT / "storage" / "state.json", ROOT.parent / "state" / "state.json", ROOT.parent / "slk_bot" / "storage" / "state.json"]:
            if candidate.exists():
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        state_info = json.load(f)
                    break
                except Exception:
                    pass

        last_run = state_info.get("last_run_time_utc")
        if last_run:
            try:
                lr_ts = pd.to_datetime(last_run, utc=True)
                diff = now_utc - lr_ts
                mins = int(diff.total_seconds() // 60)
                last_run_str = f"{lr_ts.strftime('%Y-%m-%d %H:%M:%S UTC')} ({mins}m ago)"
            except Exception:
                last_run_str = str(last_run)
        else:
            last_run_str = "First run in progress"

        first_started = state_info.get("first_started_utc")
        if first_started:
            try:
                fs_ts = pd.to_datetime(first_started, utc=True)
                age_delta = now_utc - fs_ts
                days = age_delta.days
                hours = int((age_delta.seconds) // 3600)
                state_age_str = f"{days}d {hours}h (since {fs_ts.strftime('%Y-%m-%d')})"
            except Exception:
                state_age_str = str(first_started)
        else:
            state_age_str = "New state"

        candle_times = state_info.get("last_candle_close_times", {})
        candle_lines = []
        if candle_times:
            for grid_name, ct in candle_times.items():
                candle_lines.append(f"  • {grid_name}: <code>{ct}</code>")
        else:
            candle_lines.append("  • <i>Pending next candle close evaluation</i>")

        return (
            f"🤖 <b>SLK Bias Bot Status</b>\n\n"
            f"• <b>Current Time:</b> {now_str}\n"
            f"• <b>Last Run Time:</b> {last_run_str}\n"
            f"• <b>State Age:</b> {state_age_str}\n"
            f"• <b>Last New-Candle Times:</b>\n" + "\n".join(candle_lines) + "\n\n"
            f"• <b>Active Watchlist:</b> {len(self.instruments)} instruments (USOIL excluded)\n"
            f"• <b>Total Cached Candles:</b> {total_candles:,}\n"
            f"• <b>Stored Setups:</b> {total_setups:,}\n"
            f"• <b>Total Alerts Sent:</b> {total_alerts_sent:,}\n"
            f"• <b>Mode:</b> Run-Once Job (Dry-Run: {dry_str})\n"
        )

    def _cmd_instruments(self) -> str:
        lines = [f"📋 <b>Active Watchlist Instruments ({len(self.instruments)}):</b>\n"]
        for name, inst in self.instruments.items():
            ac = inst.get("asset_class", "forex")
            align = inst.get("alignment", "forex")
            lines.append(f"• <b>{name}</b> ({ac}, align: {align})")
        return "\n".join(lines)

    def _cmd_verify(self, args: list[str]) -> str:
        if len(args) < 2:
            return "Usage: /verify <code>SYMBOL TIMEFRAME</code> (e.g. <code>/verify EURUSD D</code>)"
        sym = args[0].upper()
        tf = args[1].upper()

        df = self.cache.load_candles(sym, tf)
        if df.empty:
            return f"No cached candles found for <code>{sym}</code> on timeframe <code>{tf}</code>."

        count = len(df)
        oldest = df.index[0].strftime("%Y-%m-%d %H:%M UTC")
        newest = df.index[-1].strftime("%Y-%m-%d %H:%M UTC")
        last_close = format_price(float(df["close"].iloc[-1]), sym)

        return (
            f"🔍 <b>Verification: {sym} ({tf})</b>\n\n"
            f"• <b>Candle Count:</b> {count}\n"
            f"• <b>Oldest Closed:</b> {oldest}\n"
            f"• <b>Newest Closed:</b> {newest}\n"
            f"• <b>Latest Close:</b> {last_close}\n"
        )

    def _cmd_alerts(self, args: Optional[list[str]] = None) -> str:
        args = args or []
        if not args:
            ext = "On" if self.alert_preferences.get("external_bo", True) else "Off"
            ext_icon = "✅" if self.alert_preferences.get("external_bo", True) else "⬜"
            intl = "On" if self.alert_preferences.get("internal_bo", False) else "Off"
            intl_icon = "✅" if self.alert_preferences.get("internal_bo", False) else "⬜"
            trend_val = str(self.alert_preferences.get("trend_alignment", "required")).lower()
            trend = "Required" if trend_val == "required" else "Unrequired"
            trend_icon = "✅" if trend_val == "required" else "⬜"

            return (
                "⚙️ <b>Alert Preferences</b>\n\n"
                f"{ext_icon} <b>External BO confirmations:</b> {ext}\n"
                f"{intl_icon} <b>Internal BO confirmations:</b> {intl}\n"
                f"{trend_icon} <b>Trend alignment:</b> {trend}\n\n"
                "<i>Change settings with:</i>\n"
                "• <code>/alerts external on</code> or <code>/alerts external off</code>\n"
                "• <code>/alerts internal on</code> or <code>/alerts internal off</code>\n"
                "• <code>/alerts trend required</code> or <code>/alerts trend unrequired</code>\n\n"
                "⚠️ <i>Note: Internal BOs have lower statistical expectancy than External BOs.</i>"
            )

        sub = args[0].lower()
        if sub == "external":
            if len(args) < 2 or args[1].lower() not in ("on", "off"):
                return "Usage: <code>/alerts external on</code> or <code>/alerts external off</code>"
            val = args[1].lower() == "on"
            self.alert_preferences["external_bo"] = val
            self._save_alert_preferences()
            return f"✅ External BO confirmations set to: <b>{'On' if val else 'Off'}</b>"
        elif sub == "internal":
            if len(args) < 2 or args[1].lower() not in ("on", "off"):
                return "Usage: <code>/alerts internal on</code> or <code>/alerts internal off</code>"
            val = args[1].lower() == "on"
            self.alert_preferences["internal_bo"] = val
            self._save_alert_preferences()
            return f"✅ Internal BO confirmations set to: <b>{'On' if val else 'Off'}</b>"
        elif sub == "trend":
            if len(args) < 2 or args[1].lower() not in ("required", "unrequired"):
                return "Usage: <code>/alerts trend required</code> or <code>/alerts trend unrequired</code>"
            val = args[1].lower()
            self.alert_preferences["trend_alignment"] = val
            self._save_alert_preferences()
            return f"✅ Trend alignment requirement set to: <b>{val.title()}</b>"
        else:
            return "Unknown preference. Use <code>/alerts</code> to view current settings."

    def _save_alert_preferences(self) -> None:
        try:
            if SETTINGS_PATH.exists():
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                data["alert_preferences"] = self.alert_preferences
                with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        except Exception as e:
            log.warning("Could not persist alert preferences to settings.yaml: %s", e)

    def _cmd_terms(self) -> str:
        return (
            "📚 <b>SLK Alert Terminology & Guide</b>\n\n"
            "<b>Direction (BUY / SELL):</b>\n"
            "The projected market bias: BUY indicates upward momentum; SELL indicates downward momentum.\n\n"
            "<b>Timeframe Hierarchy (D1→H4 / W1→D1):</b>\n"
            "The first timeframe is Higher Timeframe (HTF) where key level rejection or liquidity sweep occurred. "
            "The second is Lower Timeframe (LTF) where market structure breakout confirms the setup.\n\n"
            "<b>Candle Naming & Date Conventions:</b>\n"
            "• <b>Daily Candles:</b> Named by the date TradingView displays under the candle on the daily chart "
            "(the date of the session close in New York time; e.g. candle opening Thu 21:00 UTC closes Fri 17:00 NY and is named Fri).\n"
            "• <b>Weekly Candles:</b> Named by the Monday of the trading week (e.g. Mon 12 Oct).\n"
            "• <b>H4 Candles:</b> Named by actual open date and time in West Africa Time (WAT = UTC+1, e.g. 'Mon 12 Oct 06:00 WAT').\n"
            "• <b>Alert Created:</b> Displayed in WAT (UTC+1).\n\n"
            "<b>Key Levels (KL) & Formation:</b>\n"
            "Horizontal reference levels established by 2-candle pivot formations on the level's timeframe:\n"
            "• <b>A-Shape (Peak):</b> Bullish candle followed by a bearish candle (resistance).\n"
            "• <b>V-Shape (Trough):</b> Bearish candle followed by a bullish candle (support).\n"
            "• <b>Open-Close (OCL):</b> Two consecutive candles in the same direction marking transition pivots.\n\n"
            "<b>Disrespect Caps & Retirement:</b>\n"
            "• <b>A & V Shapes:</b> Active up to 3 disrespects (0=initial, 1=RBS/SBR, 2-3=QM). Retired after more than 3 disrespects.\n"
            "• <b>Open-Close Levels:</b> Active up to 1 disrespect (1=flipped OCL). Retired after more than 1 disrespect.\n\n"
            "<b>Freshness & Age Rules:</b>\n"
            "• <b>Fresh Only:</b> Unfresh key levels do not count. Setups strictly require fresh levels that have not been tested since formation.\n"
            "• <b>Age Limits (Daily setups D1→H4):</b> No key level older than 6 months (measured at rejection candle). "
            "A level with exactly 3 disrespects is used only if 4 months old or less. Weekly setups have no age limit.\n\n"
            "<b>Rejection Candle & Breakout:</b>\n"
            "• <b>Rejection:</b> HTF candle testing key level and closing back inside, establishing extreme X.\n"
            "• <b>External BO:</b> High-probability confirmation where LTF candle closes beyond pre-rejection turning point.\n"
            "• <b>Internal BO:</b> Early confirmation where LTF candle closes beyond post-rejection swing.\n"
            "• <b>Next Day Rule:</b> Breakout candle opened after rejection candle closed.\n\n"
            "⚠️ <i>Disclaimer: Prices come from OANDA feed and may differ from other platforms by a few pips.</i>"
        )
