"""Tests for Eagle fund.update messages."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.communications.eagle_update import EagleUpdate
from app.communications.protocol import Environment


def create_valid_update() -> dict[str, object]:
    """Create one representative Eagle fund.update payload."""

    return {
        "type": "fund.update",
        "seq": 205902,
        "event_id": (
            "srv-1780644217846-o8wa9c:"
            "trail:1647.94"
        ),
        "signal_id": (
            "srv-1780644217846-o8wa9c"
        ),
        "ts": "2026-06-05T07:25:07.843Z",
        "env": "staging",
        "update": "trail_moved",
        "trail_stop": 1647.94,
        "updates": {
            "trail_stop": 1647.94,
        },
    }


def test_from_payload_parses_valid_update() -> None:
    """Valid Eagle update should parse successfully."""

    message = create_valid_update()

    update = EagleUpdate.from_payload(
        message
    )

    assert update.seq == 205902

    assert (
        update.event_id
        == (
            "srv-1780644217846-o8wa9c:"
            "trail:1647.94"
        )
    )

    assert (
        update.signal_id
        == "srv-1780644217846-o8wa9c"
    )

    assert (
        update.timestamp
        == datetime(
            2026,
            6,
            5,
            7,
            25,
            7,
            843000,
            tzinfo=timezone.utc,
        )
    )

    assert (
        update.environment
        is Environment.STAGING
    )

    assert (
        update.update_type
        == "trail_moved"
    )

    assert (
        update.trail_stop
        == Decimal("1647.94")
    )


def test_from_payload_accepts_nested_trail_stop() -> None:
    """Nested updates.trail_stop should be accepted."""

    message = create_valid_update()

    del message["trail_stop"]

    update = EagleUpdate.from_payload(
        message
    )

    assert (
        update.trail_stop
        == Decimal("1647.94")
    )


def test_from_payload_allows_no_trail_stop() -> None:
    """Future update types may not contain a trail stop."""

    message = create_valid_update()

    del message["trail_stop"]
    message["updates"] = {}

    update = EagleUpdate.from_payload(
        message
    )

    assert update.trail_stop is None


def test_from_payload_accepts_other_update_type() -> None:
    """Parser should not assume trail_moved is the only update."""

    message = create_valid_update()

    message["update"] = "some_future_update"

    update = EagleUpdate.from_payload(
        message
    )

    assert (
        update.update_type
        == "some_future_update"
    )


def test_from_payload_rejects_wrong_message_type() -> None:
    """Only fund.update belongs in EagleUpdate."""

    message = create_valid_update()

    message["type"] = "fund.entry"

    with pytest.raises(
        ValueError,
        match="type='fund.update'",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_negative_seq() -> None:
    """Negative sequence number should fail."""

    message = create_valid_update()

    message["seq"] = -1

    with pytest.raises(
        ValueError,
        match="non-negative integer",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_bool_seq() -> None:
    """Boolean must not be accepted as an integer sequence."""

    message = create_valid_update()

    message["seq"] = True

    with pytest.raises(
        ValueError,
        match="non-negative integer",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_missing_event_id() -> None:
    """Update must contain event_id."""

    message = create_valid_update()

    del message["event_id"]

    with pytest.raises(
        ValueError,
        match="'event_id'",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_missing_signal_id() -> None:
    """Update must contain signal_id."""

    message = create_valid_update()

    del message["signal_id"]

    with pytest.raises(
        ValueError,
        match="'signal_id'",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_invalid_timestamp() -> None:
    """Timestamp must be valid ISO-8601."""

    message = create_valid_update()

    message["ts"] = "not-a-timestamp"

    with pytest.raises(
        ValueError,
        match="ISO-8601",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_invalid_environment() -> None:
    """Unsupported Eagle environment should fail."""

    message = create_valid_update()

    message["env"] = "production"

    with pytest.raises(
        ValueError,
        match="unsupported environment",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_missing_update_type() -> None:
    """Update must describe its update type."""

    message = create_valid_update()

    del message["update"]

    with pytest.raises(
        ValueError,
        match="'update'",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_boolean_trail_stop() -> None:
    """Boolean trail stop should fail numeric validation."""

    message = create_valid_update()

    message["trail_stop"] = True

    with pytest.raises(
        ValueError,
        match="'trail_stop'",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_non_numeric_trail_stop() -> None:
    """Non-numeric trail stop should fail."""

    message = create_valid_update()

    message["trail_stop"] = "abc"

    with pytest.raises(
        ValueError,
        match="'trail_stop'",
    ):
        EagleUpdate.from_payload(
            message
        )


def test_from_payload_rejects_non_finite_trail_stop() -> None:
    """Infinite trail stop should fail."""

    message = create_valid_update()

    message["trail_stop"] = "Infinity"

    with pytest.raises(
        ValueError,
        match="must be finite",
    ):
        EagleUpdate.from_payload(
            message
        )