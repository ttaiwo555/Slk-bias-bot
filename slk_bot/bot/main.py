"""
slk_bot/bot/main.py
-------------------
Main bot executable and entry point for Phase 4.
Usage:
  python -m slk_bot.bot.main --dry-run
  python -m slk_bot.bot.main --scan
  python -m slk_bot.bot.main --status
"""

from __future__ import annotations

import sys
import os
import argparse
import logging
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

from dotenv import load_dotenv

load_dotenv()

from slk_bot.storage.cache import CandleCache
from slk_bot.bot.telegram import TelegramBotClient
from slk_bot.bot.price_alerts import PriceAlertManager
from slk_bot.bot.handlers import BotCommandHandler
from slk_bot.bot.scheduler import BotScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("slk_bot")


import time
import signal
import threading

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SLK Bias Bot – Phase 4 Controller")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print all messages to console instead of sending to Telegram",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Run a single scan cycle immediately and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print current bot status and exit",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Scan interval in seconds for daemon loop (default: 60)",
    )
    return parser


def run_daemon_loop(
    scheduler: BotScheduler,
    handler: BotCommandHandler,
    tg_client: TelegramBotClient,
    scan_interval_seconds: int = 60,
    price_alert_interval_seconds: int = 15,
) -> None:
    running = True

    def _sig_handler(sig, frame):
        nonlocal running
        log.info("Shutdown signal received (%s). Stopping gracefully...", sig)
        running = False

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        pass

    def scheduler_worker() -> None:
        last_scan = 0.0
        last_alert = 0.0
        while running:
            try:
                now_mono = time.monotonic()
                # 1. Check price alerts every 15s
                if now_mono - last_alert >= price_alert_interval_seconds:
                    scheduler.check_active_price_alerts()
                    last_alert = now_mono

                # 2. Scheduled scan cycle every scan_interval_seconds
                if now_mono - last_scan >= scan_interval_seconds:
                    log.info("Running scheduled candle sync & scan cycle...")
                    scheduler.sync_latest_candles(count=5)
                    scheduler.run_scan_cycle()
                    last_scan = now_mono

                time.sleep(1)
            except Exception as e:
                log.error("Scheduler worker error: %s", e, exc_info=True)
                time.sleep(5)

    sched_thread = threading.Thread(target=scheduler_worker, daemon=True, name="SchedulerWorker")
    sched_thread.start()

    log.info(
        "Live daemon loop running. Polling Telegram & scanning every %ds (Press Ctrl+C to stop)...",
        scan_interval_seconds,
    )
    last_offset: Optional[int] = None

    while running:
        try:
            # Poll Telegram for incoming updates
            updates = tg_client.get_updates(offset=last_offset, timeout=2)
            for upd in updates:
                upd_id = upd.get("update_id")
                if upd_id is not None:
                    last_offset = upd_id + 1

                msg = upd.get("message")
                if msg and "text" in msg:
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "")
                    log.info("Incoming Telegram message from %s: %s", chat_id, text)
                    reply = handler.handle_message(chat_id, text)
                    if reply:
                        tg_client.send_message(reply)

            time.sleep(0.2)

        except Exception as e:
            log.error("Exception in Telegram polling loop: %s", e)
            time.sleep(2)

    log.info("SLK Bias Bot daemon loop stopped.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Determine dry-run mode
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    is_dry_run = args.dry_run or not bool(token)

    cache = CandleCache()
    tg_client = TelegramBotClient(cache=cache, dry_run=is_dry_run)
    alert_mgr = PriceAlertManager(cache=cache)
    handler = BotCommandHandler(cache=cache, alert_manager=alert_mgr, dry_run=is_dry_run)
    scheduler = BotScheduler(cache=cache, telegram_client=tg_client, price_alert_manager=alert_mgr)

    # 1. Silent first start
    seeded = tg_client.seed_first_start_silently()
    if seeded > 0:
        log.info("Silent first-start: seeded %d existing alerts.", seeded)

    # 2. Command line immediate execution
    if args.status:
        resp = handler.handle_message(handler.owner_chat_id or "console", "/status")
        print(resp)
        return

    if args.scan:
        log.info("Executing immediate scan cycle...")
        dispatched = scheduler.run_scan_cycle()
        log.info("Scan complete. Dispatched: %d alerts.", len(dispatched))
        return

    log.info("SLK Bias Bot starting. Mode: Dry-Run=%s", is_dry_run)
    if not is_dry_run:
        num_pairs = len(scheduler.instruments)
        tg_client.send_message(f"bot live-scanning {num_pairs} pairs")

    run_daemon_loop(
        scheduler=scheduler,
        handler=handler,
        tg_client=tg_client,
        scan_interval_seconds=args.interval,
    )


if __name__ == "__main__":
    main()
