"""Tests for the Eagle fund.heartbeat control frame."""

import pytest

from app.communications.eagle_heartbeat import EagleHeartbeat


def create_valid_heartbeat() -> dict:
    """Create a realistic Eagle fund.heartbeat message."""

    return {
        "type": "fund.heartbeat",
        "seq": 1043,
        "open_count": 1,
        "open_count_by_channel": {
            "fund": 1,
            "apollo": 0,
            "hermes": 0,
            "athena": 0,
            "moab": 0,
        },
    }


def test_from_dict_creates_valid_heartbeat() -> None:
    """A valid heartbeat should create an EagleHeartbeat."""

    heartbeat = EagleHeartbeat.from_dict(
        create_valid_heartbeat()
    )

    assert heartbeat.message_type == "fund.heartbeat"
    assert heartbeat.seq == 1043
    assert heartbeat.open_count == 1

    assert heartbeat.open_count_by_channel == {
        "fund": 1,
        "apollo": 0,
        "hermes": 0,
        "athena": 0,
        "moab": 0,
    }


def test_heartbeat_is_immutable() -> None:
    """Heartbeat objects should not be modifiable after creation."""

    heartbeat = EagleHeartbeat.from_dict(
        create_valid_heartbeat()
    )

    with pytest.raises(AttributeError):
        heartbeat.seq = 2000  # type: ignore[misc]


def test_wrong_message_type_is_rejected() -> None:
    """Only fund.heartbeat messages may create EagleHeartbeat objects."""

    message = create_valid_heartbeat()
    message["type"] = "fund.entry"

    with pytest.raises(
        ValueError,
        match="'type' must be 'fund.heartbeat'",
    ):
        EagleHeartbeat.from_dict(message)


def test_missing_sequence_is_rejected() -> None:
    """Heartbeat sequence is required."""

    message = create_valid_heartbeat()
    del message["seq"]

    with pytest.raises(
        ValueError,
        match="Missing required fund.heartbeat field",
    ):
        EagleHeartbeat.from_dict(message)


def test_negative_sequence_is_rejected() -> None:
    """Heartbeat sequence numbers must not be negative."""

    message = create_valid_heartbeat()
    message["seq"] = -1

    with pytest.raises(
        ValueError,
        match="'seq' must be a non-negative integer",
    ):
        EagleHeartbeat.from_dict(message)


def test_boolean_sequence_is_rejected() -> None:
    """Boolean values must not be accepted as sequence numbers."""

    message = create_valid_heartbeat()
    message["seq"] = True

    with pytest.raises(
        ValueError,
        match="'seq' must be a non-negative integer",
    ):
        EagleHeartbeat.from_dict(message)


def test_negative_open_count_is_rejected() -> None:
    """Open-position count must not be negative."""

    message = create_valid_heartbeat()
    message["open_count"] = -1

    with pytest.raises(
        ValueError,
        match="'open_count' must be a non-negative integer",
    ):
        EagleHeartbeat.from_dict(message)


def test_invalid_open_count_by_channel_is_rejected() -> None:
    """Per-channel open counts must be supplied as a JSON object."""

    message = create_valid_heartbeat()
    message["open_count_by_channel"] = []

    with pytest.raises(
        ValueError,
        match="'open_count_by_channel' must be a JSON object",
    ):
        EagleHeartbeat.from_dict(message)