"""
run_once.py
-----------
Single entry point for SLK Bias Bot running as a "run once and exit" job
on GitHub Actions (or local cron / CLI).

Lifecycle:
1. Load state (candles.db, alerts_log.csv, state.json).
2. Clean Telegram webhook and process pending Telegram commands with getUpdates & stored offset.
3. Fast No-Op Path: evaluate per-instrument grids (Forex 17:00 NY, Metals 18:00 NY, Crypto 00:00 UTC).
   If no new candle has closed and no command ran, save last run timestamp and exit in seconds.
4. If new candle closed or /scan forced:
   - Incrementally fetch latest closed candles.
   - Detect triggers and evaluate setup lifecycles.
   - Dispatch newly confirmed alerts to Telegram (deduped).
   - Log alerts to alerts_log.csv.
   - Update last candle close times.
5. Check weekly heartbeat (send every 7 days).
6. Save state and exit cleanly.
"""

from __future__ import annotations

import sys
import os
import json
import logging
import argparse
import datetime
from pathlib import Path

# Force UTF-8 encoding on Windows without breaking test runners
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv()

# Setup logging (Never log secrets)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("run_once")

ROOT = Path(__file__).resolve().parent

# Ensure local imports work
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.bot.telegram import TelegramBotClient
from slk_bot.bot.handlers import BotCommandHandler
from slk_bot.bot.scheduler import BotScheduler
from slk_bot.bot.grids import (
    get_all_latest_closed_candle_times,
    evaluate_new_candles_closed,
    UTC_TZ,
)


def get_state_file_paths() -> list[Path]:
    """All paths where state.json must be kept synchronized."""
    return [
        ROOT / "slk_bot" / "storage" / "state.json",
        ROOT / "state" / "state.json",
    ]


def load_state() -> dict:
    best_state: dict = {}
    best_mtime: float = -1.0

    for path in get_state_file_paths():
        if path.exists():
            try:
                mtime = path.stat().st_mtime
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Prioritize state file with higher run_count or most recent mtime
                if not best_state or data.get("run_count", 0) > best_state.get("run_count", 0) or mtime > best_mtime:
                    best_state = data
                    best_mtime = mtime
                    log.info("Loaded state candidate from %s (run #%d, offset %s)", path, data.get("run_count", 0), data.get("last_update_id", 0))
            except Exception as e:
                log.warning("Could not read state file %s (%s).", path, e)

    if best_state:
        return best_state

    now_iso = datetime.datetime.now(UTC_TZ).isoformat()
    return {
        "last_update_id": 0,
        "last_run_time_utc": None,
        "first_started_utc": now_iso,
        "last_candle_close_times": {},
        "last_heartbeat_time_utc": None,
        "run_count": 0,
    }


def save_state(state: dict) -> None:
    """Synchronously persist state to all canonical locations."""
    for path in get_state_file_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temp_path = path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temp_path.replace(path)
            log.info("State successfully synced to %s (run #%d, offset %s)", path, state.get("run_count", 0), state.get("last_update_id", 0))
        except Exception as e:
            log.error("Failed to save state to %s: %s", path, e)


def maybe_send_heartbeat(
    state: dict,
    telegram: TelegramBotClient,
    cache: CandleCache,
    now_utc: datetime.datetime,
    dry_run: bool,
) -> bool:
    """
    Sends the weekly heartbeat message at most once every 7 days,
    based on the persisted 'last_heartbeat_time_utc' timestamp in state.
    """
    if dry_run or not telegram.token or not telegram.chat_id:
        return False

    last_hb = state.get("last_heartbeat_time_utc")
    send_heartbeat = False

    if not last_hb:
        first_start = state.get("first_started_utc")
        if first_start:
            try:
                fs_dt = datetime.datetime.fromisoformat(first_start)
                if fs_dt.tzinfo is None:
                    fs_dt = fs_dt.replace(tzinfo=UTC_TZ)
                if (now_utc - fs_dt).total_seconds() >= 7 * 86400:
                    send_heartbeat = True
                else:
                    state["last_heartbeat_time_utc"] = now_utc.isoformat()
                    save_state(state)
            except Exception:
                send_heartbeat = True
        else:
            state["last_heartbeat_time_utc"] = now_utc.isoformat()
            save_state(state)
    else:
        try:
            hb_dt = datetime.datetime.fromisoformat(last_hb)
            if hb_dt.tzinfo is None:
                hb_dt = hb_dt.replace(tzinfo=UTC_TZ)
            diff_sec = (now_utc - hb_dt).total_seconds()
            if diff_sec >= 7 * 86400:  # 7 days = 604,800 seconds
                send_heartbeat = True
        except Exception as e:
            log.warning("Could not parse last_heartbeat_time_utc '%s': %s", last_hb, e)
            send_heartbeat = False

    if send_heartbeat:
        log.info("Sending weekly heartbeat to Telegram...")
        with cache._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM alerts_sent")
            total_alerts_sent = cur.fetchone()[0]

        first_start = state.get("first_started_utc", now_utc.isoformat())
        try:
            fs_dt = datetime.datetime.fromisoformat(first_start)
            days_active = (now_utc - fs_dt).days
        except Exception:
            days_active = 0

        # Dynamic active instruments count
        active_count = 39
        try:
            from slk_bot.bot.scheduler import INST_DATA
            active_count = len([
                inst for inst in INST_DATA.get("instruments", [])
                if not (inst.get("exclude_from_triggers") or inst.get("name") == "USOIL")
            ])
        except Exception:
            pass

        heartbeat_msg = (
            "💓 <b>SLK Bias Bot – Weekly Heartbeat</b>\n\n"
            "• <b>Status:</b> All systems operational on GitHub Actions cron\n"
            f"• <b>Watchlist:</b> {active_count} active instruments (USOIL excluded)\n"
            "• <b>Total Runs:</b> {run_count:,}\n"
            "• <b>Total Alerts Sent:</b> {total_alerts_sent:,}\n"
            "• <b>Uptime:</b> Active for {days_active} days\n\n"
            "<i>Bot will continue monitoring every 15 minutes.</i>"
        ).format(
            run_count=state.get("run_count", 1),
            total_alerts_sent=total_alerts_sent,
            days_active=days_active,
        )

        telegram.send_message(heartbeat_msg)
        state["last_heartbeat_time_utc"] = now_utc.isoformat()
        save_state(state)
        log.info("Weekly heartbeat dispatched and timestamp persisted: %s", state["last_heartbeat_time_utc"])
        return True

    return False


def parse_args() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SLK Bias Bot – Run Once & Exit Job")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without dispatching live messages to Telegram (outputs to console)",
    )
    parser.add_argument(
        "--silent-first-start",
        action="store_true",
        help="Seed historical alerts/state silently without sending alerts to Telegram",
    )
    parser.add_argument(
        "--force-scan",
        action="store_true",
        help="Force a full incremental fetch and scan cycle even if no new candle closed on the grid",
    )
    return parser


def notify_failure(telegram: TelegramBotClient, error_msg: str) -> None:
    """Attempt to notify authorized owner on Telegram if run failed."""
    try:
        text = (
            "⚠️ <b>SLK Bot Run Error</b>\n\n"
            f"An error occurred during execution:\n<code>{error_msg}</code>\n\n"
            "Please check GitHub Actions run logs for the full traceback."
        )
        telegram.send_message(text)
    except Exception as e:
        log.warning("Could not send failure notification to Telegram: %s", e)


def main() -> int:
    parser = parse_args()
    args = parser.parse_args()

    # Secrets from environment (never hardcoded, never logged)
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    oanda_key = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_API_TOKEN")
    oanda_account = os.getenv("OANDA_ACCOUNT_ID")

    dry_run = args.dry_run or not bool(bot_token)
    if dry_run:
        log.info("Running in DRY-RUN mode.")

    now_utc = datetime.datetime.now(UTC_TZ)
    log.info("Starting SLK Bot run-once execution at %s", now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"))

    # Load persistent state
    state = load_state()
    state["run_count"] = state.get("run_count", 0) + 1

    # Initialize components
    cache = CandleCache()
    telegram = TelegramBotClient(token=bot_token, chat_id=chat_id, cache=cache, dry_run=dry_run)
    handler = BotCommandHandler(
        owner_chat_id=chat_id,
        cache=cache,
        dry_run=dry_run,
        state_getter=lambda: state,
    )

    # Make sure no webhook is set so getUpdates polling works cleanly
    telegram.delete_webhook()

    # Step 1: Process pending Telegram commands via getUpdates
    last_update_id = state.get("last_update_id", 0)
    force_scan = args.force_scan
    commands_processed = 0

    try:
        updates = telegram.get_updates(offset=last_update_id, timeout=1)
        for update in updates:
            up_id = update.get("update_id", 0)
            last_update_id = max(last_update_id, up_id + 1)

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            msg_chat_id = str(message.get("chat", {}).get("id", "")).strip()
            text = message.get("text", "").strip()

            # Security: ignore any chat other than authorized TELEGRAM_CHAT_ID
            if not handler.is_authorized(msg_chat_id):
                log.warning("Dropped message from unauthorized chat_id: %s", msg_chat_id)
                continue

            log.info("Processing command from owner: %s", text)
            commands_processed += 1
            if text.lower().startswith("/scan"):
                force_scan = True

            resp = handler.handle_message(msg_chat_id, text)
            if resp:
                telegram.send_message(resp)

        # Requirement 1: Save Telegram offset immediately after processing commands
        if last_update_id != state.get("last_update_id", 0):
            state["last_update_id"] = last_update_id
            save_state(state)
            log.info("Telegram offset updated to %d and saved immediately to prevent duplicate replies.", last_update_id)

    except Exception as e:
        log.warning("Error processing Telegram getUpdates: %s", e)

    # Step 2: Weekly Heartbeat Message (at most once every 7 days)
    maybe_send_heartbeat(state, telegram, cache, now_utc, dry_run)

    # Step 3: Grid close check (Fast No-Op path)
    current_grid_times = get_all_latest_closed_candle_times(now_utc)
    stored_grid_times = state.get("last_candle_close_times", {})
    has_new_candles, new_grids = evaluate_new_candles_closed(current_grid_times, stored_grid_times)

    log.info("Grid status: has_new_candles=%s (grids: %s), force_scan=%s", has_new_candles, new_grids, force_scan)

    # FAST NO-OP PATH:
    # If no new candle has closed on any grid and no scan requested, exit within seconds!
    if not has_new_candles and not force_scan:
        log.info("⚡ Fast no-op path: No new candle has closed across any grid and no scan requested.")
        state["last_run_time_utc"] = now_utc.isoformat()
        save_state(state)
        log.info("SLK Bot finished in fast no-op mode (0 alerts). Total runs: %d", state["run_count"])
        return 0

    # Step 4: Run incremental sync & setup evaluation
    try:
        log.info("Initializing BotScheduler for scan cycle...")
        scheduler = BotScheduler(cache=cache, telegram_client=telegram)

        # 4a. Incrementally fetch latest candles
        log.info("Syncing latest closed candles incrementally...")
        updated_count = scheduler.sync_latest_candles(count=5)
        log.info("Incremental candle sync updated %d instruments.", updated_count)

        # 4b. Evaluate setup lifecycles & detect new confirmations
        log.info("Running setup evaluation across active watchlist...")
        dispatched_alerts = scheduler.run_scan_cycle()

        # Handle silent first start if requested
        if args.silent_first_start:
            log.info("Silent first start active: seeding alerts without sending messages.")
            telegram.seed_first_start_silently()

        # Update stored candle close times upon successful scan
        state["last_candle_close_times"] = current_grid_times
        state["last_run_time_utc"] = now_utc.isoformat()
        save_state(state)
        log.info("Scan completed successfully. Total confirmed alerts dispatched: %d", len(dispatched_alerts))

    except Exception as e:
        log.error("Fatal error during scan cycle: %s", e, exc_info=True)
        notify_failure(telegram, str(e))
        state["last_run_time_utc"] = now_utc.isoformat()
        save_state(state)
        return 1

    log.info("SLK Bot run-once execution completed cleanly. Total runs: %d", state["run_count"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
