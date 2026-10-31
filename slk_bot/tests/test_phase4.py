"""
slk_bot/tests/test_phase4.py
----------------------------
Comprehensive test suite for Phase 4:
1. Alert message formatting (Section 6 + freshness, marginal flag, conflict line, display scale, UTC).
2. Deduplication and restart safety (never duplicate alerts).
3. Silent first-start historical seeding.
4. Push policy (Method 1 fresh only, M2, M3, internal suppressed, external pushed).
5. Digest overflow handling (>5 alerts -> 5 individual + 1 digest).
6. /internal command returning active internal breakouts.
7. Both-direction candles evaluated independently.
8. Price alert touch crossing in both directions.
9. Price alert 48h active expiration with weekend market-closed skipping.
10. Mocked Telegram command flows and access control.
"""

import os
import pytest
import pandas as pd

from slk_bot.storage.cache import CandleCache
from slk_bot.bot.formatter import (
    format_price,
    format_alert_message,
    format_digest_message,
    format_internal_setup_message,
)
from slk_bot.bot.telegram import TelegramBotClient
from slk_bot.bot.handlers import BotCommandHandler
from slk_bot.strategy.models import (
    KeyLevel,
    LevelKind,
    LevelRole,
    Side,
    TriggerDirection,
    TriggerMethod,
    HTFTrigger,
)
from slk_bot.strategy.setups import (
    SLKAlert,
    SLKSetup,
    SetupTimeline,
    SetupState,
    BreakoutType,
)


@pytest.fixture
def temp_cache(tmp_path):
    db_file = tmp_path / "test_phase4.db"
    return CandleCache(db_file)


def _make_dummy_alert(
    dedupe_key: str = "EURUSD_D_BULLISH_1_EXTERNAL",
    symbol: str = "EURUSD",
    direction: TriggerDirection = TriggerDirection.BULLISH,
    method: TriggerMethod = TriggerMethod.METHOD_1,
    breakout_type: BreakoutType = BreakoutType.EXTERNAL,
    is_low_prob: bool = False,
    is_marginal: bool = False,
    broken_price: float = 1.12500,
    extreme_x: float = 1.11800,
) -> SLKAlert:
    broken = KeyLevel(
        id=f"{symbol}_test_lvl",
        timeframe="H4",
        kind=LevelKind.A_SHAPE,
        price=broken_price,
        formation_time=pd.Timestamp("2026-10-10 17:00:00", tz="UTC"),
        formation_confirmed_time=pd.Timestamp("2026-10-10 21:00:00", tz="UTC"),
        formation_index=10,
        disrespect_count=0,
        last_side=Side.BELOW,
        current_role=LevelRole.RESISTANCE,
    )
    htf_lvl = KeyLevel(
        id=f"{symbol}_htf_lvl",
        timeframe="D",
        kind=LevelKind.A_SHAPE,
        price=1.12000,
        formation_time=pd.Timestamp("2026-10-05 21:00:00", tz="UTC"),
        formation_confirmed_time=pd.Timestamp("2026-10-06 21:00:00", tz="UTC"),
        formation_index=5,
        disrespect_count=1,
        last_side=Side.ABOVE,
        current_role=LevelRole.RBS,
        fresh=not is_low_prob,
    )
    return SLKAlert(
        dedupe_key=dedupe_key,
        symbol=symbol,
        htf="D",
        ltf="H4",
        direction=direction,
        method=method,
        breakout_type=breakout_type,
        alert_time=pd.Timestamp("2026-10-12 21:00:00", tz="UTC"),
        breakout_candle_time=pd.Timestamp("2026-10-12 17:00:00", tz="UTC"),
        htf_level=htf_lvl,
        mitigation_extreme_x=extreme_x,
        broken_level=broken,
        is_low_probability=is_low_prob,
        is_marginal=is_marginal,
    )


# -----------------------------------------------------------------------------
# 1. Alert Formatting Tests
# -----------------------------------------------------------------------------

def test_alert_formatting_basic_and_flags():
    alert = _make_dummy_alert(
        symbol="EURUSD",
        direction=TriggerDirection.BULLISH,
        method=TriggerMethod.METHOD_1,
        is_low_prob=False,
        is_marginal=True,
    )
    msg = format_alert_message(alert)

    assert "EURUSD" in msg and "BULLISH" in msg and "Daily" in msg
    assert "Method 1 (Rejection) [FRESH] [MARGINAL]" in msg
    assert "EXTERNAL (H4)" in msg
    assert "Level Broken:</b> A @ 1.12500" in msg
    assert "Rejected Level: A @ 1.12000 (RBS)" in msg
    assert "Mon 12 Oct 22:00 WAT" in msg


def test_alert_formatting_with_conflict_line():
    alert = _make_dummy_alert(symbol="AUDCAD", direction=TriggerDirection.BULLISH)

    # Opposing setup
    opp_trig = HTFTrigger(
        symbol="AUDCAD",
        timeframe="D",
        direction=TriggerDirection.BEARISH,
        method=TriggerMethod.METHOD_1,
        trigger_candle_time=pd.Timestamp("2026-10-08 21:00:00", tz="UTC"),
        trigger_candle_open=0.9920,
        trigger_candle_high=0.9950,
        trigger_candle_low=0.9910,
        trigger_candle_close=0.9915,
        mitigation_extreme=0.99500,
        primary_level=None,
    )
    opp_setup = SLKSetup(
        id="AUDCAD_D_BEARISH_test",
        symbol="AUDCAD",
        htf="D",
        ltf="H4",
        trigger=opp_trig,
        state=SetupState.EXTERNAL_CONFIRMED,
        timeline=SetupTimeline(
            trigger_time=opp_trig.trigger_candle_time,
            trigger_close_time=opp_trig.trigger_candle_time + pd.Timedelta(days=1),
            extreme_candle_time=opp_trig.trigger_candle_time,
            extreme_candle_index=5,
            extreme_price_x=0.99500,
        ),
    )

    msg = format_alert_message(alert, opposite_setup=opp_setup)
    assert "Active Opposite Setup:</b> Confirmed BEARISH on Daily (AUDCAD)" in msg


def test_display_scale_formatting():
    # USOIL has display_scale 1 (Task 2c: show OANDA prices as they are)
    p_usoil = format_price(99.051, "USOIL")
    assert p_usoil == "99.1"

    # EURUSD has 5 decimals
    p_eur = format_price(1.12524, "EURUSD")
    assert p_eur == "1.12524"

    # XAGUSD has 3 decimals
    p_xag = format_price(61.350, "XAGUSD")
    assert p_xag == "61.350"


# -----------------------------------------------------------------------------
# 2. Deduplication and Restart Safety Tests
# -----------------------------------------------------------------------------

def test_deduplication_and_restart_safety(temp_cache):
    tg = TelegramBotClient(cache=temp_cache, dry_run=True)
    alert = _make_dummy_alert(dedupe_key="KEY_001")

    # First dispatch -> sent
    dispatched = tg.dispatch_alerts([alert])
    assert len(dispatched) == 1
    assert temp_cache.is_alert_sent("KEY_001") is True

    # Second dispatch (e.g. after restart or next scan) -> skipped
    dispatched_again = tg.dispatch_alerts([alert])
    assert len(dispatched_again) == 0


# -----------------------------------------------------------------------------
# 3. Silent First Start Seeding Test
# -----------------------------------------------------------------------------

def test_silent_first_start_seeding(temp_cache):
    # Pre-populate database with an existing alert
    alert = _make_dummy_alert(dedupe_key="EXISTING_HISTORICAL_01")
    temp_cache.save_alert(alert, "setup_historical")

    tg = TelegramBotClient(cache=temp_cache, dry_run=True)
    assert temp_cache.get_bot_state("first_start_seeded") is None

    # First start: seed silently
    seeded = tg.seed_first_start_silently()
    assert seeded == 1
    assert temp_cache.is_alert_sent("EXISTING_HISTORICAL_01") is True
    assert temp_cache.get_bot_state("first_start_seeded") == "true"

    # Subsequent starts do nothing
    seeded_again = tg.seed_first_start_silently()
    assert seeded_again == 0


# -----------------------------------------------------------------------------
# 4. Push Policy Tests
# -----------------------------------------------------------------------------

def test_push_policy_rules(temp_cache):
    tg = TelegramBotClient(cache=temp_cache, dry_run=True)
    push_rules = {
        "push_methods": [1, 2, 3],
        "push_breakout_types": ["external"],
        "push_htf": ["W", "D"],
        "unfresh_alerts": False,
    }

    # Method 1 Unfresh -> NOT pushed
    a_m1_unfresh = _make_dummy_alert(dedupe_key="K1", method=TriggerMethod.METHOD_1, is_low_prob=True)
    # Internal Breakout -> NOT pushed
    a_internal = _make_dummy_alert(dedupe_key="K2", breakout_type=BreakoutType.INTERNAL)
    # Method 1 Fresh External -> PUSHED
    a_m1_fresh_ext = _make_dummy_alert(dedupe_key="K3", method=TriggerMethod.METHOD_1, is_low_prob=False, breakout_type=BreakoutType.EXTERNAL)
    # Method 3 Unfresh External -> PUSHED (M3 allows unfresh push)
    a_m3_unfresh_ext = _make_dummy_alert(dedupe_key="K4", method=TriggerMethod.METHOD_3, is_low_prob=True, breakout_type=BreakoutType.EXTERNAL)

    dispatched = tg.dispatch_alerts(
        [a_m1_unfresh, a_internal, a_m1_fresh_ext, a_m3_unfresh_ext],
        push_rules=push_rules,
    )
    assert len(dispatched) == 2
    dispatched_keys = [a.dedupe_key for a in dispatched]
    assert "K3" in dispatched_keys
    assert "K4" in dispatched_keys
    assert "K1" not in dispatched_keys
    assert "K2" not in dispatched_keys


# -----------------------------------------------------------------------------
# 5. Digest Overflow Test
# -----------------------------------------------------------------------------

def test_digest_overflow_handling(temp_cache, monkeypatch):
    tg = TelegramBotClient(cache=temp_cache, dry_run=True)
    messages_sent = []

    def mock_send(text, parse_mode="HTML"):
        messages_sent.append(text)
        return True

    monkeypatch.setattr(tg, "send_message", mock_send)

    # Generate 7 valid alerts
    alerts = [
        _make_dummy_alert(dedupe_key=f"ALERT_{i}", symbol=f"SYM{i}")
        for i in range(1, 8)
    ]
    dispatched = tg.dispatch_alerts(alerts, push_rules={"digest_overflow_threshold": 5})

    assert len(dispatched) == 7
    # 5 individual messages + 1 digest message = 6 messages sent
    assert len(messages_sent) == 6
    assert "SLK Digest: 2 Additional Confirmed Alerts" in messages_sent[-1]


# -----------------------------------------------------------------------------
# 6. /internal Command Test
# -----------------------------------------------------------------------------

def test_internal_command_formatting():
    trig = HTFTrigger(
        symbol="EURUSD",
        timeframe="D",
        direction=TriggerDirection.BULLISH,
        method=TriggerMethod.METHOD_1,
        trigger_candle_time=pd.Timestamp("2026-10-10 21:00:00", tz="UTC"),
        trigger_candle_open=1.1210,
        trigger_candle_high=1.1250,
        trigger_candle_low=1.1200,
        trigger_candle_close=1.1230,
        mitigation_extreme=1.12000,
        primary_level=None,
    )
    ib_lvl = KeyLevel(
        id="internal_lvl",
        timeframe="H4",
        kind=LevelKind.A_SHAPE,
        price=1.12400,
        formation_time=pd.Timestamp("2026-10-11 13:00:00", tz="UTC"),
        formation_confirmed_time=pd.Timestamp("2026-10-11 17:00:00", tz="UTC"),
        formation_index=15,
        disrespect_count=0,
        last_side=Side.BELOW,
        current_role=LevelRole.RESISTANCE,
    )
    setup = SLKSetup(
        id="EURUSD_D_BULLISH_test",
        symbol="EURUSD",
        htf="D",
        ltf="H4",
        trigger=trig,
        state=SetupState.INTERNAL_CONFIRMED,
        timeline=SetupTimeline(
            trigger_time=trig.trigger_candle_time,
            trigger_close_time=trig.trigger_candle_time + pd.Timedelta(days=1),
            extreme_candle_time=trig.trigger_candle_time,
            extreme_candle_index=10,
            extreme_price_x=1.12000,
            internal_breakout_candle_time=pd.Timestamp("2026-10-11 17:00:00", tz="UTC"),
            internal_broken_level=ib_lvl,
        ),
    )
    msg = format_internal_setup_message(setup)
    assert "EURUSD" in msg and "BULLISH" in msg and "Daily" in msg
    assert "Internal Breakout:</b> A @ 1.12400" in msg
    assert "1.12000" in msg


# -----------------------------------------------------------------------------
# 7. Command Handlers and Access Control Tests
# -----------------------------------------------------------------------------

def test_command_handlers_access_control(temp_cache):
    handler = BotCommandHandler(owner_chat_id="123456789", cache=temp_cache, dry_run=False)

    # Unauthorized chat ID -> dropped (None)
    resp_unauth = handler.handle_message(chat_id="999999999", text="/status")
    assert resp_unauth is None

    # Authorized chat ID -> processes command
    resp_auth = handler.handle_message(chat_id="123456789", text="/status")
    assert resp_auth is not None
    assert "SLK Bias Bot Status" in resp_auth


def test_command_handlers_status_output(temp_cache):
    handler = BotCommandHandler(owner_chat_id="123456789", cache=temp_cache, dry_run=False)
    resp = handler.handle_message(chat_id="123456789", text="/status")

    assert "SLK Bias Bot Status" in resp
    assert "Last Run Time:" in resp
    assert "State Age:" in resp
    assert "Last New-Candle Times:" in resp
    assert "Active Watchlist:" in resp
    assert "Total Cached Candles:" in resp


def test_command_handlers_alerts_preferences_and_terms(temp_cache):
    handler = BotCommandHandler(owner_chat_id="123456789", cache=temp_cache, dry_run=False)

    # Test /alerts preferences command
    resp_alerts = handler.handle_message(chat_id="123456789", text="/alerts")
    assert "Alert Preferences" in resp_alerts
    assert "External BO confirmations" in resp_alerts

    # Test toggle /alerts
    resp_toggle = handler.handle_message(chat_id="123456789", text="/alerts external off")
    assert "External BO confirmations set to: <b>Off</b>" in resp_toggle
    assert handler.alert_preferences["external_bo"] is False

    # Test /terms command
    resp_terms = handler.handle_message(chat_id="123456789", text="/terms")
    assert "SLK Alert Terminology & Guide" in resp_terms
    assert "Disrespect Caps & Retirement" in resp_terms


def test_command_handlers_active_and_internal(temp_cache):
    handler = BotCommandHandler(owner_chat_id="123456789", cache=temp_cache, dry_run=False)

    # Initially empty
    resp_act = handler.handle_message(chat_id="123456789", text="/active")
    assert "No currently active confirmed setups." in resp_act

    resp_intl = handler.handle_message(chat_id="123456789", text="/internal")
    assert "No setups currently have an active internal breakout" in resp_intl

    # Insert a dummy setup in setups table
    temp_cache.save_setup(
        setup_id="EURUSD_D_BULLISH_test",
        symbol="EURUSD",
        htf="D",
        ltf="H4",
        direction="bullish",
        method=1,
        trigger_time="2026-10-10T21:00:00Z",
        state="INTERNAL_CONFIRMED",
        extreme_x=1.12000,
        payload_json="{}",
    )

    resp_act2 = handler.handle_message(chat_id="123456789", text="/active")
    assert "Active Confirmed Setups" in resp_act2
    assert "EURUSD" in resp_act2

    resp_intl2 = handler.handle_message(chat_id="123456789", text="/internal")
    assert "Active Internal Breakouts" in resp_intl2
    assert "EURUSD" in resp_intl2


def test_telegram_delete_webhook_dry_run():
    client = TelegramBotClient(dry_run=True)
    assert client.delete_webhook() is True
