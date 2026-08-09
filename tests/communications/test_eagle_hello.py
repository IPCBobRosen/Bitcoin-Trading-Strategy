"""Tests for the Eagle fund.hello control frame."""

from datetime import datetime, timezone

import pytest

from app.communications.eagle_hello import EagleHello
from app.communications.protocol import Environment


def create_valid_hello() -> dict:
    """Create a realistic Eagle fund.hello message."""

    return {
        "type": "fund.hello",
        "contract": "1.2.0",
        "version": "1.2.0",
        "capabilities": [
            "size_mult",
            "fund.add",
            "funding_rate",
            "p14_disable",
        ],
        "flags": {
            "p14_disabled": True,
            "size_mult_enabled": False,
            "pyramid_enabled": False,
        },
        "last_seq": 1042,
        "since_seq": 0,
        "open_count": 1,
        "open": [
            {
                "signal_id": "srv-1780315691411-npw9s8",
                "play_id": "P11",
            }
        ],
        "replay_count": 0,
        "ts": "2026-07-13T17:04:37.456Z",
        "env": "live",
    }


def test_from_dict_creates_valid_hello() -> None:
    """A valid fund.hello should create an EagleHello."""

    hello = EagleHello.from_dict(
        create_valid_hello()
    )

    assert hello.message_type == "fund.hello"
    assert hello.contract == "1.2.0"
    assert hello.version == "1.2.0"

    assert hello.capabilities == (
        "size_mult",
        "fund.add",
        "funding_rate",
        "p14_disable",
    )

    assert hello.flags["p14_disabled"] is True

    assert hello.last_seq == 1042
    assert hello.since_seq == 0
    assert hello.open_count == 1
    assert hello.replay_count == 0

    assert hello.environment is Environment.LIVE

    assert hello.timestamp == datetime(
        2026,
        7,
        13,
        17,
        4,
        37,
        456000,
        tzinfo=timezone.utc,
    )

    assert (
        hello.open_positions[0]["signal_id"]
        == "srv-1780315691411-npw9s8"
    )


def test_open_by_channel_is_optional() -> None:
    """Fund-only hello messages need not contain open_by_channel."""

    hello = EagleHello.from_dict(
        create_valid_hello()
    )

    assert hello.open_by_channel is None


def test_open_by_channel_is_preserved_when_supplied() -> None:
    """Combined-stream hello should preserve channel snapshots."""

    message = create_valid_hello()

    message["open_by_channel"] = {
        "fund": [
            {
                "signal_id": "fund-signal-001",
            }
        ],
        "apollo": [],
    }

    hello = EagleHello.from_dict(message)

    assert hello.open_by_channel is not None
    assert "fund" in hello.open_by_channel
    assert "apollo" in hello.open_by_channel


def test_wrong_message_type_is_rejected() -> None:
    """Only fund.hello messages may create EagleHello objects."""

    message = create_valid_hello()
    message["type"] = "fund.entry"

    with pytest.raises(
        ValueError,
        match="'type' must be 'fund.hello'",
    ):
        EagleHello.from_dict(message)


def test_missing_required_field_is_rejected() -> None:
    """Required hello fields must be present."""

    message = create_valid_hello()
    del message["last_seq"]

    with pytest.raises(
        ValueError,
        match="Missing required fund.hello field",
    ):
        EagleHello.from_dict(message)


def test_negative_last_seq_is_rejected() -> None:
    """Sequence cursors must not be negative."""

    message = create_valid_hello()
    message["last_seq"] = -1

    with pytest.raises(
        ValueError,
        match="'last_seq' must be a non-negative integer",
    ):
        EagleHello.from_dict(message)


def test_invalid_capabilities_are_rejected() -> None:
    """Capabilities must be strings."""

    message = create_valid_hello()
    message["capabilities"] = [
        "size_mult",
        123,
    ]

    with pytest.raises(
        ValueError,
        match="'capabilities' must contain only strings",
    ):
        EagleHello.from_dict(message)


def test_open_count_must_match_open_positions() -> None:
    """open_count must agree with the supplied open snapshot."""

    message = create_valid_hello()
    message["open_count"] = 2

    with pytest.raises(
        ValueError,
        match="'open_count' must match",
    ):
        EagleHello.from_dict(message)


def test_invalid_environment_is_rejected() -> None:
    """Hello environment must be staging or live."""

    message = create_valid_hello()
    message["env"] = "production"

    with pytest.raises(
        ValueError,
        match="'env' must be either 'staging' or 'live'",
    ):
        EagleHello.from_dict(message)