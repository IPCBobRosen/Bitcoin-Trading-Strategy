"""Tests for the SQLite EventStore."""

from pathlib import Path

import pytest

from app.event_store import EventProcessingResult, EventStore


def test_new_store_has_no_processed_events(
    tmp_path: Path,
) -> None:
    """A new SQLite store should not contain event IDs."""

    store = EventStore(tmp_path / "events.db")

    assert store.has_processed_event("event-001") is False


def test_mark_event_processed_persists_event(
    tmp_path: Path,
) -> None:
    """A processed event ID should be stored in SQLite."""

    database_path = tmp_path / "events.db"

    store = EventStore(database_path)
    store.mark_event_processed("event-001")

    reopened_store = EventStore(database_path)

    assert reopened_store.has_processed_event("event-001") is True


def test_check_and_mark_event_rejects_duplicate(
    tmp_path: Path,
) -> None:
    """The same event ID should not be accepted twice."""

    store = EventStore(tmp_path / "events.db")

    first_result = store.check_and_mark_event("event-001")
    second_result = store.check_and_mark_event("event-001")

    assert first_result is True
    assert second_result is False


def test_new_store_has_no_sequence(
    tmp_path: Path,
) -> None:
    """A new SQLite store should not have a sequence cursor."""

    store = EventStore(tmp_path / "events.db")

    assert store.get_last_seq() is None


def test_mark_seq_processed_persists_sequence(
    tmp_path: Path,
) -> None:
    """The last durable sequence should survive reopening the store."""

    database_path = tmp_path / "events.db"

    store = EventStore(database_path)
    store.mark_seq_processed(100)

    reopened_store = EventStore(database_path)

    assert reopened_store.get_last_seq() == 100


def test_sequence_cursor_only_moves_forward(
    tmp_path: Path,
) -> None:
    """Older sequence values must not move the cursor backward."""

    store = EventStore(tmp_path / "events.db")

    store.mark_seq_processed(100)
    store.mark_seq_processed(90)

    assert store.get_last_seq() == 100


def test_check_and_mark_seq_accepts_newer_sequence(
    tmp_path: Path,
) -> None:
    """A newer sequence should be accepted and persisted."""

    store = EventStore(tmp_path / "events.db")

    assert store.check_and_mark_seq(100) is True
    assert store.check_and_mark_seq(150) is True
    assert store.get_last_seq() == 150


def test_check_and_mark_seq_rejects_same_sequence(
    tmp_path: Path,
) -> None:
    """The same sequence should not be accepted twice."""

    store = EventStore(tmp_path / "events.db")

    assert store.check_and_mark_seq(100) is True
    assert store.check_and_mark_seq(100) is False


def test_check_and_mark_seq_rejects_older_sequence(
    tmp_path: Path,
) -> None:
    """An older sequence should not be accepted."""

    store = EventStore(tmp_path / "events.db")

    assert store.check_and_mark_seq(100) is True
    assert store.check_and_mark_seq(90) is False


def test_invalid_event_id_is_rejected(
    tmp_path: Path,
) -> None:
    """An empty event ID should be rejected."""

    store = EventStore(tmp_path / "events.db")

    with pytest.raises(
        ValueError,
        match="'event_id' must be a non-empty string",
    ):
        store.check_and_mark_event("")


def test_invalid_sequence_is_rejected(
    tmp_path: Path,
) -> None:
    """A negative sequence should be rejected."""

    store = EventStore(tmp_path / "events.db")

    with pytest.raises(
        ValueError,
        match="'seq' must be a non-negative integer",
    ):
        store.check_and_mark_seq(-1)

def test_atomic_event_and_sequence_are_persisted_together(
    tmp_path: Path,
) -> None:
    """A new event should persist both event ID and sequence."""

    database_path = tmp_path / "events.db"

    store = EventStore(database_path)

    result = store.check_and_mark_event_with_seq(
        "event-001",
        100,
    )

    reopened_store = EventStore(database_path)

    assert result == EventProcessingResult.ACCEPTED
    assert reopened_store.has_processed_event("event-001") is True
    assert reopened_store.get_last_seq() == 100


def test_atomic_duplicate_event_changes_nothing(
    tmp_path: Path,
) -> None:
    """A duplicate event ID should not advance the sequence cursor."""

    store = EventStore(tmp_path / "events.db")

    first_result = store.check_and_mark_event_with_seq(
        "event-001",
        100,
    )

    duplicate_result = store.check_and_mark_event_with_seq(
        "event-001",
        200,
    )

    assert first_result == EventProcessingResult.ACCEPTED
    assert duplicate_result == EventProcessingResult.DUPLICATE_EVENT
    assert store.get_last_seq() == 100


def test_atomic_out_of_sequence_event_is_not_persisted(
    tmp_path: Path,
) -> None:
    """An old sequence with a new event ID should persist nothing."""

    store = EventStore(tmp_path / "events.db")

    store.check_and_mark_event_with_seq(
        "event-100",
        100,
    )

    result = store.check_and_mark_event_with_seq(
        "event-090",
        90,
    )

    assert result == EventProcessingResult.OUT_OF_SEQUENCE
    assert store.has_processed_event("event-090") is False
    assert store.get_last_seq() == 100


def test_atomic_sequence_gap_is_allowed(
    tmp_path: Path,
) -> None:
    """A sequence gap should still be accepted."""

    store = EventStore(tmp_path / "events.db")

    store.check_and_mark_event_with_seq(
        "event-100",
        100,
    )

    result = store.check_and_mark_event_with_seq(
        "event-150",
        150,
    )

    assert result == EventProcessingResult.ACCEPTED
    assert store.has_processed_event("event-150") is True
    assert store.get_last_seq() == 150

def test_check_event_with_seq_does_not_mutate_state(tmp_path) -> None:
    """Read-only event eligibility checks must not persist event or sequence."""

    database_path = tmp_path / "events.db"
    store = EventStore(database_path)

    result = store.check_event_with_seq(
        "event-100",
        100,
    )

    assert result is EventProcessingResult.ACCEPTED

    # Read-only eligibility check must not consume the event.
    assert store.has_processed_event("event-100") is False

    # It must not advance the durable sequence cursor either.
    assert store.get_last_seq() is None

    # The normal durable operation must still be able to accept it afterward.
    durable_result = store.check_and_mark_event_with_seq(
        "event-100",
        100,
    )

    assert durable_result is EventProcessingResult.ACCEPTED
    assert store.has_processed_event("event-100") is True
    assert store.get_last_seq() == 100

def test_check_event_with_seq_detects_duplicate_without_mutating_state(
    tmp_path,
) -> None:
    """Read-only check must identify an already processed event."""

    database_path = tmp_path / "events.db"
    store = EventStore(database_path)

    durable_result = store.check_and_mark_event_with_seq(
        "event-100",
        100,
    )

    assert durable_result is EventProcessingResult.ACCEPTED

    result = store.check_event_with_seq(
        "event-100",
        101,
    )

    assert result is EventProcessingResult.DUPLICATE_EVENT

    # Read-only check must not advance the cursor.
    assert store.get_last_seq() == 100


def test_check_event_with_seq_detects_out_of_sequence_without_mutating_state(
    tmp_path,
) -> None:
    """Read-only check must reject an older sequence without mutation."""

    database_path = tmp_path / "events.db"
    store = EventStore(database_path)

    durable_result = store.check_and_mark_event_with_seq(
        "event-100",
        100,
    )

    assert durable_result is EventProcessingResult.ACCEPTED

    result = store.check_event_with_seq(
        "event-99",
        99,
    )

    assert result is EventProcessingResult.OUT_OF_SEQUENCE

    # New event ID must not have been persisted.
    assert store.has_processed_event("event-99") is False

    # Read-only check must not move the durable cursor.
    assert store.get_last_seq() == 100

def test_has_processed_event_is_read_only(
    tmp_path: Path,
) -> None:
    """has_processed_event must never mutate event or sequence state."""

    database_path = tmp_path / "events.db"
    store = EventStore(database_path)

    durable_result = store.check_and_mark_event_with_seq(
        "existing-event",
        50,
    )

    assert durable_result is EventProcessingResult.ACCEPTED
    assert store.get_last_seq() == 50

    # Existing event is correctly recognized.
    assert store.has_processed_event("existing-event") is True

    # Missing event is correctly reported absent.
    assert store.has_processed_event("missing-event") is False

    # Read-only lookups must not move the durable cursor.
    assert store.get_last_seq() == 50

    # A lookup for a missing event must not accidentally persist it.
    assert store.has_processed_event("missing-event") is False

    # Existing durable event remains intact.
    assert store.has_processed_event("existing-event") is True
    assert store.get_last_seq() == 50