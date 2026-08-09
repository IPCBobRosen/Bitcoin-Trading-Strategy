"""Tests for the Eagle HeartbeatProcessor."""

from pathlib import Path

import pytest

from app.communications.eagle_heartbeat import EagleHeartbeat
from app.event_store import EventStore
from app.heartbeat_processor import HeartbeatProcessor


def create_heartbeat(
    *,
    seq: int = 100,
) -> EagleHeartbeat:
    """Create a valid heartbeat for processor tests."""

    return EagleHeartbeat.from_dict(
        {
            "type": "fund.heartbeat",
            "seq": seq,
            "open_count": 0,
            "open_count_by_channel": {
                "fund": 0,
                "apollo": 0,
                "hermes": 0,
                "athena": 0,
                "moab": 0,
            },
        }
    )


def test_process_persists_heartbeat_sequence(
    tmp_path: Path,
) -> None:
    """A heartbeat should advance the durable sequence cursor."""

    database_path = tmp_path / "events.db"

    store = EventStore(database_path)
    processor = HeartbeatProcessor(store)

    processor.process(
        create_heartbeat(seq=100)
    )

    reopened_store = EventStore(database_path)

    assert reopened_store.get_last_seq() == 100


def test_newer_heartbeat_advances_sequence(
    tmp_path: Path,
) -> None:
    """A newer heartbeat should advance the durable cursor."""

    store = EventStore(tmp_path / "events.db")
    processor = HeartbeatProcessor(store)

    processor.process(
        create_heartbeat(seq=100)
    )

    processor.process(
        create_heartbeat(seq=150)
    )

    assert store.get_last_seq() == 150


def test_older_heartbeat_does_not_move_sequence_backward(
    tmp_path: Path,
) -> None:
    """An older heartbeat must not move the cursor backward."""

    store = EventStore(tmp_path / "events.db")
    processor = HeartbeatProcessor(store)

    processor.process(
        create_heartbeat(seq=100)
    )

    processor.process(
        create_heartbeat(seq=90)
    )

    assert store.get_last_seq() == 100


def test_same_heartbeat_sequence_is_safe(
    tmp_path: Path,
) -> None:
    """Processing the same heartbeat sequence twice should be harmless."""

    store = EventStore(tmp_path / "events.db")
    processor = HeartbeatProcessor(store)

    processor.process(
        create_heartbeat(seq=100)
    )

    processor.process(
        create_heartbeat(seq=100)
    )

    assert store.get_last_seq() == 100


def test_heartbeat_cursor_survives_store_reopen(
    tmp_path: Path,
) -> None:
    """Heartbeat sequence progress must survive a BTS restart."""

    database_path = tmp_path / "events.db"

    first_store = EventStore(database_path)
    first_processor = HeartbeatProcessor(first_store)

    first_processor.process(
        create_heartbeat(seq=250)
    )

    reopened_store = EventStore(database_path)

    assert reopened_store.get_last_seq() == 250


def test_invalid_heartbeat_object_is_rejected(
    tmp_path: Path,
) -> None:
    """The processor must accept only validated EagleHeartbeat objects."""

    store = EventStore(tmp_path / "events.db")
    processor = HeartbeatProcessor(store)

    with pytest.raises(
        TypeError,
        match="'heartbeat' must be an EagleHeartbeat",
    ):
        processor.process(  # type: ignore[arg-type]
            {"seq": 100}
        )


def test_invalid_event_store_is_rejected() -> None:
    """HeartbeatProcessor requires a real EventStore."""

    with pytest.raises(
        TypeError,
        match="'event_store' must be an EventStore",
    ):
        HeartbeatProcessor(  # type: ignore[arg-type]
            "events.db"
        )