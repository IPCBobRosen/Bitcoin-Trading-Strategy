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