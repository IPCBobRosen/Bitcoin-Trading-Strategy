"""Tests for lifecycle events received from Eagle."""

from datetime import datetime, timezone
from typing import Any

import pytest

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import Environment


def create_valid_message() -> dict[str, Any]:
    """Create a valid Eagle lifecycle-event message for testing."""

    return {
        "type": "fund.entry",
        "seq": 1204,
        "event_id": "event-001",
        "signal_id": "signal-001",
        "ts": "2026-08-01T09:01:00+00:00",
        "env": "staging",
        "payload": {
            "signal": {
                "symbol": "BTCUSDT",
            }
        },
    }


def create_real_eagle_entry_message() -> dict[str, Any]:
    """Create a message shaped like a real Eagle fund.entry frame."""

    return {
        "type": "fund.entry",
        "seq": 202269,
        "event_id": "bt-1777605420000-BTCUSDT-0:entry",
        "signal_id": "bt-1777605420000-BTCUSDT-0",
        "ts": "2026-05-01T03:17:00.000Z",
        "env": "staging",
        "signal": {
            "cascade": True,
            "direction": "long",
            "entry": 76714.6,
            "play_id": "P11",
            "signal_id": "bt-1777605420000-BTCUSDT-0",
            "size_band": "missing_funding",
            "size_mult": 1,
            "stop": 76484.46,
            "symbol": "BTCUSDT",
            "target": 77174.89,
        },
    }


def test_from_dict_creates_valid_event() -> None:
    """A valid Eagle message should create a lifecycle event."""

    message = create_valid_message()

    event = IncomingLifecycleEvent.from_dict(message)

    assert event.message_type == "fund.entry"
    assert event.seq == 1204
    assert event.event_id == "event-001"
    assert event.signal_id == "signal-001"

    assert event.timestamp == datetime(
        2026,
        8,
        1,
        9,
        1,
        tzinfo=timezone.utc,
    )

    assert event.environment is Environment.STAGING

    assert event.payload == {
        "signal": {
            "symbol": "BTCUSDT",
        }
    }


def test_from_dict_preserves_real_eagle_top_level_signal() -> None:
    """A real Eagle top-level signal should be preserved in payload."""

    message = create_real_eagle_entry_message()

    event = IncomingLifecycleEvent.from_dict(message)

    assert event.message_type == "fund.entry"
    assert event.seq == 202269

    assert event.payload["signal"] == message["signal"]

    signal = event.payload["signal"]

    assert signal["symbol"] == "BTCUSDT"
    assert signal["direction"] == "long"
    assert signal["size_mult"] == 1
    assert signal["play_id"] == "P11"


def test_top_level_signal_overrides_payload_signal() -> None:
    """The real top-level Eagle signal is authoritative."""

    message = create_real_eagle_entry_message()

    message["payload"] = {
        "signal": {
            "symbol": "SHOULD_NOT_BE_USED",
        },
        "existing_field": "preserved",
    }

    event = IncomingLifecycleEvent.from_dict(message)

    assert event.payload["signal"] == message["signal"]
    assert event.payload["existing_field"] == "preserved"


def test_from_dict_allows_exit_without_signal() -> None:
    """A fund.exit frame may legitimately omit the signal object."""

    message = {
        "type": "fund.exit",
        "seq": 202274,
        "event_id": "bt-1777605420000-BTCUSDT-0:exit",
        "signal_id": "bt-1777605420000-BTCUSDT-0",
        "ts": "2026-05-01T04:18:59.999Z",
        "env": "staging",
        "closed_at": "2026-05-01T04:18:59.999Z",
        "exit_price": 77191.06,
        "outcome": "trail",
        "realized_r": 1.937,
    }

    event = IncomingLifecycleEvent.from_dict(message)

    assert event.message_type == "fund.exit"
    assert event.payload == {}


def test_from_dict_rejects_non_object_top_level_signal() -> None:
    """A supplied top-level signal must be a JSON object."""

    message = create_real_eagle_entry_message()
    message["signal"] = "BTCUSDT"

    with pytest.raises(
        ValueError,
        match="'signal' must be a JSON object when supplied",
    ):
        IncomingLifecycleEvent.from_dict(message)


def test_from_dict_rejects_missing_signal_id() -> None:
    """A message without signal_id should be rejected."""

    message = create_valid_message()
    del message["signal_id"]

    with pytest.raises(
        ValueError,
        match=r"Missing required field\(s\): signal_id",
    ):
        IncomingLifecycleEvent.from_dict(message)


def test_from_dict_rejects_invalid_environment() -> None:
    """An unsupported environment should be rejected."""

    message = create_valid_message()
    message["env"] = "production"

    with pytest.raises(
        ValueError,
        match="'env' must be either 'staging' or 'live'",
    ):
        IncomingLifecycleEvent.from_dict(message)


def test_from_dict_rejects_negative_sequence() -> None:
    """A negative sequence number should be rejected."""

    message = create_valid_message()
    message["seq"] = -1

    with pytest.raises(
        ValueError,
        match="'seq' must be a non-negative integer",
    ):
        IncomingLifecycleEvent.from_dict(message)