"""Tests for the EventProcessor."""

from pathlib import Path

import pytest

from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore


def create_test_event(
    *,
    event_id: str = "event-001",
    signal_id: str = "signal-001",
    seq: int = 1,
) -> IncomingLifecycleEvent:
    """Create a valid Eagle lifecycle event for processor tests."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": seq,
            "event_id": event_id,
            "signal_id": signal_id,
            "ts": "2026-08-09T12:00:00+00:00",
            "env": "staging",
            "payload": {
                "intent": "BUY_TO_OPEN",
            },
        }
    )


def test_new_event_is_accepted(
    tmp_path: Path,
) -> None:
    """A new event with a newer sequence should be accepted."""

    store = EventStore(tmp_path / "events.db")
    processor = EventProcessor(store)

    event = create_test_event()

    result = processor.process(event)

    assert result.accepted is True
    assert result.status is EventProcessStatus.ACCEPTED


def test_duplicate_event_is_rejected(
    tmp_path: Path,
) -> None:
    """The same event ID must not be accepted twice."""

    store = EventStore(tmp_path / "events.db")
    processor = EventProcessor(store)

    event = create_test_event()

    first_result = processor.process(event)
    second_result = processor.process(event)

    assert first_result.status is EventProcessStatus.ACCEPTED
    assert second_result.accepted is False
    assert (
        second_result.status
        is EventProcessStatus.DUPLICATE_EVENT
    )


def test_older_sequence_is_rejected(
    tmp_path: Path,
) -> None:
    """A new event ID with an older sequence should be rejected."""

    store = EventStore(tmp_path / "events.db")
    processor = EventProcessor(store)

    newer_event = create_test_event(
        event_id="event-100",
        signal_id="signal-100",
        seq=100,
    )

    older_event = create_test_event(
        event_id="event-090",
        signal_id="signal-090",
        seq=90,
    )

    first_result = processor.process(newer_event)
    second_result = processor.process(older_event)

    assert first_result.status is EventProcessStatus.ACCEPTED
    assert second_result.accepted is False
    assert (
        second_result.status
        is EventProcessStatus.OUT_OF_SEQUENCE
    )


def test_same_sequence_with_new_event_id_is_rejected(
    tmp_path: Path,
) -> None:
    """A new event ID using the current durable seq should be rejected."""

    store = EventStore(tmp_path / "events.db")
    processor = EventProcessor(store)

    first_event = create_test_event(
        event_id="event-001",
        signal_id="signal-001",
        seq=100,
    )

    second_event = create_test_event(
        event_id="event-002",
        signal_id="signal-002",
        seq=100,
    )

    processor.process(first_event)
    result = processor.process(second_event)

    assert result.accepted is False
    assert result.status is EventProcessStatus.OUT_OF_SEQUENCE


def test_sequence_gap_is_allowed(
    tmp_path: Path,
) -> None:
    """A higher sequence should be accepted even when numbers are skipped."""

    store = EventStore(tmp_path / "events.db")
    processor = EventProcessor(store)

    first_event = create_test_event(
        event_id="event-100",
        signal_id="signal-100",
        seq=100,
    )

    second_event = create_test_event(
        event_id="event-150",
        signal_id="signal-150",
        seq=150,
    )

    first_result = processor.process(first_event)
    second_result = processor.process(second_event)

    assert first_result.status is EventProcessStatus.ACCEPTED
    assert second_result.status is EventProcessStatus.ACCEPTED


def test_processing_survives_store_reopen(
    tmp_path: Path,
) -> None:
    """Duplicate protection must survive reopening the SQLite database."""

    database_path = tmp_path / "events.db"

    first_store = EventStore(database_path)
    first_processor = EventProcessor(first_store)

    event = create_test_event(
        event_id="event-persistent-001",
        seq=100,
    )

    first_result = first_processor.process(event)

    second_store = EventStore(database_path)
    second_processor = EventProcessor(second_store)

    second_result = second_processor.process(event)

    assert first_result.status is EventProcessStatus.ACCEPTED
    assert (
        second_result.status
        is EventProcessStatus.DUPLICATE_EVENT
    )


def test_invalid_event_object_is_rejected(
    tmp_path: Path,
) -> None:
    """EventProcessor should accept only IncomingLifecycleEvent objects."""

    store = EventStore(tmp_path / "events.db")
    processor = EventProcessor(store)

    with pytest.raises(
        TypeError,
        match="'event' must be an IncomingLifecycleEvent",
    ):
        processor.process("not-an-event")  # type: ignore[arg-type]