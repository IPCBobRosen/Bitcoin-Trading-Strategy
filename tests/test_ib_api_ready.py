"""Tests for Interactive Brokers API handshake readiness."""

import pytest

from app.ib_api_ready import (
    IBApiReady,
    IBApiReadySnapshot,
)


def test_new_tracker_is_not_ready() -> None:
    """A new IB API session should not be handshake-ready."""

    readiness = IBApiReady()

    assert readiness.ready is False


def test_new_tracker_has_no_next_valid_id() -> None:
    """No IB order ID should exist before nextValidId."""

    readiness = IBApiReady()

    assert readiness.next_valid_order_id is None


def test_record_next_valid_id_marks_ready() -> None:
    """nextValidId should mark the API handshake complete."""

    readiness = IBApiReady()

    readiness.record_next_valid_id(
        100
    )

    assert readiness.ready is True


def test_record_next_valid_id_is_preserved() -> None:
    """The most recent IBKR order ID should be retained."""

    readiness = IBApiReady()

    readiness.record_next_valid_id(
        12345
    )

    assert readiness.next_valid_order_id == 12345


def test_zero_order_id_is_allowed() -> None:
    """IBKR nextValidId may be represented by zero."""

    readiness = IBApiReady()

    readiness.record_next_valid_id(
        0
    )

    assert readiness.ready is True
    assert readiness.next_valid_order_id == 0


def test_negative_order_id_is_rejected() -> None:
    """Negative IB order IDs must not mark readiness."""

    readiness = IBApiReady()

    with pytest.raises(
        ValueError,
        match="'order_id' must be a non-negative integer",
    ):
        readiness.record_next_valid_id(
            -1
        )

    assert readiness.ready is False


def test_boolean_order_id_is_rejected() -> None:
    """Boolean values must not be interpreted as order IDs."""

    readiness = IBApiReady()

    with pytest.raises(
        ValueError,
        match="'order_id' must be a non-negative integer",
    ):
        readiness.record_next_valid_id(
            True  # type: ignore[arg-type]
        )

    assert readiness.ready is False


def test_non_integer_order_id_is_rejected() -> None:
    """IBKR order ID must be an integer."""

    readiness = IBApiReady()

    with pytest.raises(
        ValueError,
        match="'order_id' must be a non-negative integer",
    ):
        readiness.record_next_valid_id(
            1.5  # type: ignore[arg-type]
        )


def test_reset_clears_ready_state() -> None:
    """Connection loss should invalidate handshake readiness."""

    readiness = IBApiReady()

    readiness.record_next_valid_id(
        100
    )

    readiness.reset()

    assert readiness.ready is False


def test_reset_clears_order_id() -> None:
    """Reset should discard the previous API session order ID."""

    readiness = IBApiReady()

    readiness.record_next_valid_id(
        100
    )

    readiness.reset()

    assert readiness.next_valid_order_id is None


def test_new_next_valid_id_after_reset_restores_ready() -> None:
    """A new session can become ready again after reset."""

    readiness = IBApiReady()

    readiness.record_next_valid_id(
        100
    )

    readiness.reset()

    readiness.record_next_valid_id(
        200
    )

    assert readiness.ready is True
    assert readiness.next_valid_order_id == 200


def test_snapshot_returns_current_state() -> None:
    """Snapshot should report the current handshake state."""

    readiness = IBApiReady()

    readiness.record_next_valid_id(
        321
    )

    result = readiness.snapshot()

    assert result == IBApiReadySnapshot(
        ready=True,
        next_valid_order_id=321,
    )


def test_snapshot_before_ready_is_not_ready() -> None:
    """Snapshot should reflect an uninitialized API session."""

    readiness = IBApiReady()

    result = readiness.snapshot()

    assert result.ready is False
    assert result.next_valid_order_id is None


def test_snapshot_is_immutable() -> None:
    """Readiness snapshots must not be mutable."""

    readiness = IBApiReady()

    result = readiness.snapshot()

    with pytest.raises(
        AttributeError,
    ):
        result.ready = True  # type: ignore[misc]