"""Tests for the IdempotencyManager."""

import pytest

from app.idempotency_manager import IdempotencyManager


def test_new_event_has_not_been_processed() -> None:
    """A new event ID should not initially be marked as processed."""

    manager = IdempotencyManager()

    assert manager.has_processed("event-001") is False


def test_mark_processed_records_event_id() -> None:
    """An event ID should be remembered after it is marked processed."""

    manager = IdempotencyManager()

    manager.mark_processed("event-001")

    assert manager.has_processed("event-001") is True


def test_check_and_mark_accepts_new_event() -> None:
    """The first occurrence of an event ID should be accepted."""

    manager = IdempotencyManager()

    result = manager.check_and_mark("event-001")

    assert result is True
    assert manager.has_processed("event-001") is True


def test_check_and_mark_rejects_duplicate_event() -> None:
    """The same event ID should not be accepted twice."""

    manager = IdempotencyManager()

    first_result = manager.check_and_mark("event-001")
    second_result = manager.check_and_mark("event-001")

    assert first_result is True
    assert second_result is False


def test_different_event_ids_are_tracked_independently() -> None:
    """Different Eagle event IDs should not interfere with one another."""

    manager = IdempotencyManager()

    first_result = manager.check_and_mark("event-001")
    second_result = manager.check_and_mark("event-002")

    assert first_result is True
    assert second_result is True


def test_empty_event_id_is_rejected() -> None:
    """An empty event ID should be rejected."""

    manager = IdempotencyManager()

    with pytest.raises(
        ValueError,
        match="'event_id' must be a non-empty string",
    ):
        manager.check_and_mark("")