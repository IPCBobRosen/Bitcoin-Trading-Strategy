"""Tests for Eagle replay tracking."""

import pytest

from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.replay_tracker import ReplayTracker


def create_hello(
    *,
    last_seq: int = 5,
    since_seq: int = 2,
    replay_count: int = 2,
) -> EagleHello:
    """Create a valid EagleHello for replay tests."""

    return EagleHello.from_dict(
        {
            "type": "fund.hello",
            "contract": "1.2.0",
            "version": "1.2.0",
            "capabilities": [],
            "flags": {},
            "last_seq": last_seq,
            "since_seq": since_seq,
            "open_count": 0,
            "open": [],
            "replay_count": replay_count,
            "ts": "2026-08-10T01:00:00+00:00",
            "env": "staging",
        }
    )


def create_event(
    *,
    seq: int,
    event_id: str,
) -> IncomingLifecycleEvent:
    """Create a valid lifecycle event for replay tests."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": seq,
            "event_id": event_id,
            "signal_id": f"signal-{seq}",
            "ts": "2026-08-10T01:00:00+00:00",
            "env": "staging",
            "payload": {
                "intent": "BUY_TO_OPEN",
            },
        }
    )


def test_new_tracker_has_no_hello() -> None:
    """A new tracker should not consider replay complete."""

    tracker = ReplayTracker()

    assert tracker.hello_received is False
    assert tracker.expected_replay_count == 0
    assert tracker.processed_replay_count == 0
    assert tracker.server_last_seq is None
    assert tracker.requested_since_seq is None
    assert tracker.replay_complete is False


def test_process_hello_initializes_replay_state() -> None:
    """fund.hello should initialize replay tracking."""

    tracker = ReplayTracker()

    tracker.process_hello(
        create_hello(
            last_seq=5,
            since_seq=2,
            replay_count=2,
        )
    )

    assert tracker.hello_received is True
    assert tracker.expected_replay_count == 2
    assert tracker.processed_replay_count == 0
    assert tracker.server_last_seq == 5
    assert tracker.requested_since_seq == 2
    assert tracker.replay_complete is False


def test_zero_replay_count_is_immediately_complete() -> None:
    """A hello with replay_count zero should be complete immediately."""

    tracker = ReplayTracker()

    tracker.process_hello(
        create_hello(
            replay_count=0
        )
    )

    assert tracker.replay_complete is True


def test_replay_completes_after_expected_events() -> None:
    """Replay should complete after the announced lifecycle count."""

    tracker = ReplayTracker()

    tracker.process_hello(
        create_hello(
            replay_count=2
        )
    )

    tracker.record_lifecycle_event(
        create_event(
            seq=3,
            event_id="event-003",
        )
    )

    assert tracker.processed_replay_count == 1
    assert tracker.replay_complete is False

    tracker.record_lifecycle_event(
        create_event(
            seq=5,
            event_id="event-005",
        )
    )

    assert tracker.processed_replay_count == 2
    assert tracker.replay_complete is True


def test_extra_lifecycle_events_do_not_increase_replay_count() -> None:
    """Live events after replay completion must not inflate replay count."""

    tracker = ReplayTracker()

    tracker.process_hello(
        create_hello(
            replay_count=1
        )
    )

    tracker.record_lifecycle_event(
        create_event(
            seq=3,
            event_id="event-003",
        )
    )

    tracker.record_lifecycle_event(
        create_event(
            seq=4,
            event_id="event-004",
        )
    )

    assert tracker.processed_replay_count == 1
    assert tracker.replay_complete is True


def test_record_event_before_hello_is_rejected() -> None:
    """Replay events must not be counted before fund.hello."""

    tracker = ReplayTracker()

    with pytest.raises(
        RuntimeError,
        match="before fund.hello",
    ):
        tracker.record_lifecycle_event(
            create_event(
                seq=1,
                event_id="event-001",
            )
        )


def test_invalid_hello_object_is_rejected() -> None:
    """ReplayTracker requires a validated EagleHello."""

    tracker = ReplayTracker()

    with pytest.raises(
        TypeError,
        match="'hello' must be an EagleHello",
    ):
        tracker.process_hello(  # type: ignore[arg-type]
            {"replay_count": 2}
        )


def test_invalid_event_object_is_rejected() -> None:
    """ReplayTracker requires validated lifecycle events."""

    tracker = ReplayTracker()

    tracker.process_hello(
        create_hello()
    )

    with pytest.raises(
        TypeError,
        match="'event' must be an IncomingLifecycleEvent",
    ):
        tracker.record_lifecycle_event(  # type: ignore[arg-type]
            {"seq": 3}
        )