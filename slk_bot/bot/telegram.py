"""
slk_bot/bot/telegram.py
-----------------------
Telegram client adapter with:
- Dry-run mode (console printing when token missing or --dry-run enabled)
- Strictly authorized chat ID handling
- Dedupe check & restart safety via SQLite
- Silent first-start historical seeding
- Digest overflow handling (first 5 individual, rest digest)
"""

from __future__ import annotations

import os
import logging
from typing import Optional, Any
import requests
from dotenv import load_dotenv

from slk_bot.storage.cache import CandleCache
from slk_bot.bot.formatter import format_alert_message, format_digest_message
from slk_bot.strategy.setups import SLKAlert, SLKSetup

load_dotenv()
log = logging.getLogger(__name__)


class TelegramBotClient:
    """Telegram Bot API client with dry-run support and push deduplication."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        cache: Optional[CandleCache] = None,
        dry_run: bool = False,
    ) -> None:
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_OWNER_CHAT_ID", "")).strip()
        self.cache = cache or CandleCache()
        self.dry_run = dry_run or not bool(self.token)

        if self.dry_run:
            log.info("TelegramBotClient initialized in DRY-RUN mode (console output).")
        else:
            log.info("TelegramBotClient initialized for live chat_id: %s", self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send a text message to the authorized chat_id, or print to console if dry-run.
        """
        if self.dry_run or not self.token:
            print("\n" + "="*60)
            print(f"[DRY-RUN TELEGRAM SEND to {self.chat_id or 'CONSOLE'}]:")
            print(text)
            print("="*60 + "\n")
            return True

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            log.error("Telegram API error %d: %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            log.error("Failed to send Telegram message: %e", e)
            return False

    def get_updates(self, offset: Optional[int] = None, timeout: int = 10) -> list[dict[str, Any]]:
        """
        Poll for incoming updates from Telegram via getUpdates long-polling.
        Returns a list of raw update dicts.
        """
        if self.dry_run or not self.token:
            return []

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        try:
            resp = requests.get(url, params=params, timeout=timeout + 5)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", [])
            log.warning("Telegram getUpdates error %d: %s", resp.status_code, resp.text)
            return []
        except Exception as e:
            log.warning("Telegram getUpdates request failed: %s", e)
            return []

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        """
        Delete any configured Telegram webhook so that getUpdates polling works cleanly.
        """
        if self.dry_run or not self.token:
            return True

        url = f"https://api.telegram.org/bot{self.token}/deleteWebhook"
        try:
            resp = requests.post(url, json={"drop_pending_updates": drop_pending_updates}, timeout=10)
            if resp.status_code == 200:
                log.info("Telegram webhook successfully cleared.")
                return True
            log.warning("Telegram deleteWebhook returned %d: %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            log.warning("Telegram deleteWebhook request failed: %s", e)
            return False

    def seed_first_start_silently(self) -> int:
        """
        On first startup, seed all existing historical setups/alerts in alerts_sent
        so the owner does not receive a flood of past alerts.
        """
        is_seeded = self.cache.get_bot_state("first_start_seeded")
        if is_seeded == "true":
            return 0

        # Query all existing alerts from database
        all_alerts = self.cache.get_alerts()
        seeded_count = 0
        for alert in all_alerts:
            dedupe_key = alert["dedupe_key"]
            if not self.cache.is_alert_sent(dedupe_key):
                self.cache.mark_alert_sent(dedupe_key, message_text="[SEEDED ON FIRST START]")
                seeded_count += 1

        self.cache.set_bot_state("first_start_seeded", "true")
        log.info("First start completed silently: seeded %d historical alerts.", seeded_count)
        return seeded_count

    def dispatch_alerts(
        self,
        alerts: list[SLKAlert],
        opposite_setups_map: Optional[dict[str, SLKSetup]] = None,
        push_rules: Optional[dict[str, Any]] = None,
    ) -> list[SLKAlert]:
        """
        Filter alerts by push policy, deduplicate against SQLite,
        and send up to 5 individually, bundling any excess into a digest message.
        Returns the list of alerts actually dispatched.
        """
        if not alerts:
            return []

        rules = push_rules or {}
        allowed_methods = rules.get("push_methods", [1, 2, 3])
        allowed_breakouts = [b.lower() for b in rules.get("push_breakout_types", ["external"])]
        allowed_htf = rules.get("push_htf", ["W", "D"])
        allow_unfresh = rules.get("unfresh_alerts", False)
        overflow_threshold = rules.get("digest_overflow_threshold", 5)

        due_alerts: list[tuple[SLKAlert, str]] = []

        for a in alerts:
            # Check push policy
            if int(a.method) not in allowed_methods:
                continue
            if a.breakout_type.value.lower() not in allowed_breakouts:
                continue
            if a.htf not in allowed_htf:
                continue
            # Method 1 unfresh setups never pushed (listed in /lowprob)
            if int(a.method) == 1 and a.is_low_probability and not allow_unfresh:
                continue

            # Dedupe check
            if self.cache.is_alert_sent(a.dedupe_key):
                continue

            # Check opposite confirmed setup on same symbol
            opp_setup = None
            if opposite_setups_map:
                opp_setup = opposite_setups_map.get(a.symbol)

            msg = format_alert_message(a, opposite_setup=opp_setup)
            due_alerts.append((a, msg))

        if not due_alerts:
            return []

        dispatched: list[SLKAlert] = []

        # If <= overflow_threshold (5), send all individually
        if len(due_alerts) <= overflow_threshold:
            for a, msg in due_alerts:
                success = self.send_message(msg)
                if success:
                    self.cache.mark_alert_sent(a.dedupe_key, message_text=msg)
                    dispatched.append(a)
        else:
            # Send first 5 individually
            for a, msg in due_alerts[:overflow_threshold]:
                success = self.send_message(msg)
                if success:
                    self.cache.mark_alert_sent(a.dedupe_key, message_text=msg)
                    dispatched.append(a)

            # Send remaining in one digest message
            overflow_list = [a for a, _ in due_alerts[overflow_threshold:]]
            digest_msg = format_digest_message(overflow_list)
            success = self.send_message(digest_msg)
            if success:
                for a in overflow_list:
                    self.cache.mark_alert_sent(a.dedupe_key, message_text="[SENT IN DIGEST]")
                    dispatched.append(a)

        return dispatched
