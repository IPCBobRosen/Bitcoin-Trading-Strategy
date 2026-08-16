"""Tests for Eagle durable stale/out-of-order event handling."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore

from scripts.test_eagle_stale_out_of_order_events import (
    INITIAL_SEQ,
    NEWER_SEQ,
    OLDER_TIMESTAMP_SEQ,
    EagleOrderingTestResult,
    build_event,
    print_result,
    require_status,
    run_eagle_ordering_test,
)


def create_success_result() -> EagleOrderingTestResult:
    """Create one fully successful ordering result."""

    return EagleOrderingTestResult(
        first_status=EventProcessStatus.ACCEPTED,
        duplicate_status=EventProcessStatus.DUPLICATE_EVENT,
        equal_seq_status=EventProcessStatus.OUT_OF_SEQUENCE,
        older_seq_status=EventProcessStatus.OUT_OF_SEQUENCE,
        cursor_after_rejections=INITIAL_SEQ,
        equal_seq_event_persisted=False,
        older_seq_event_persisted=False,
        newer_status=EventProcessStatus.ACCEPTED,
        cursor_after_newer=NEWER_SEQ,
        restart_cursor=NEWER_SEQ,
        first_event_survived_restart=True,
        newer_event_survived_restart=True,
        older_timestamp_newer_seq_status=(
            EventProcessStatus.ACCEPTED
        ),
        final_cursor=OLDER_TIMESTAMP_SEQ,
    )


def test_sequence_constants_are_ordered() -> None:
    """Harness should exercise increasingly newer sequences."""

    assert INITIAL_SEQ == 100
    assert NEWER_SEQ == 101
    assert OLDER_TIMESTAMP_SEQ == 102


def test_build_event_creates_valid_event() -> None:
    """Helper should create a validated lifecycle event."""

    event = build_event(
        event_id="event-100",
        seq=100,
    )

    assert event.event_id == "event-100"
    assert event.seq == 100
    assert event.signal_id == "eagle-ordering-signal-001"
    assert event.environment.value == "staging"
    assert event.payload["intent"] == "BUY_TO_OPEN"


def test_build_event_rejects_naive_timestamp() -> None:
    """Harness should require timezone-aware timestamps."""

    with pytest.raises(
        ValueError,
        match="'timestamp'",
    ):
        build_event(
            event_id="event-100",
            seq=100,
            timestamp=datetime(
                2026,
                8,
                16,
                12,
                0,
            ),
        )


def test_first_event_is_accepted(
    tmp_path,
) -> None:
    """First durable Eagle event should be accepted."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    result = processor.process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    assert result.status is EventProcessStatus.ACCEPTED
    assert store.get_last_seq() == 100


def test_duplicate_event_id_is_rejected(
    tmp_path,
) -> None:
    """Same Eagle event ID must never process twice."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    duplicate = processor.process(
        build_event(
            event_id="event-100",
            seq=200,
        )
    )

    assert (
        duplicate.status
        is EventProcessStatus.DUPLICATE_EVENT
    )


def test_duplicate_with_newer_seq_does_not_advance_cursor(
    tmp_path,
) -> None:
    """Duplicate identity must not corrupt sequence state."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    processor.process(
        build_event(
            event_id="event-100",
            seq=999,
        )
    )

    assert store.get_last_seq() == 100


def test_equal_sequence_is_out_of_sequence(
    tmp_path,
) -> None:
    """Different event with equal seq must fail closed."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="event-a",
            seq=100,
        )
    )

    result = processor.process(
        build_event(
            event_id="event-b",
            seq=100,
        )
    )

    assert (
        result.status
        is EventProcessStatus.OUT_OF_SEQUENCE
    )


def test_older_sequence_is_out_of_sequence(
    tmp_path,
) -> None:
    """Older unseen event must fail closed."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    result = processor.process(
        build_event(
            event_id="event-099",
            seq=99,
        )
    )

    assert (
        result.status
        is EventProcessStatus.OUT_OF_SEQUENCE
    )


def test_rejected_equal_seq_event_is_not_persisted(
    tmp_path,
) -> None:
    """Rejected unseen event must remain eligible for audit/recovery."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="accepted",
            seq=100,
        )
    )

    processor.process(
        build_event(
            event_id="rejected",
            seq=100,
        )
    )

    assert (
        store.has_processed_event(
            "rejected"
        )
        is False
    )


def test_rejected_older_seq_event_is_not_persisted(
    tmp_path,
) -> None:
    """Old unseen event should not be marked processed."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="accepted",
            seq=100,
        )
    )

    processor.process(
        build_event(
            event_id="old",
            seq=99,
        )
    )

    assert (
        store.has_processed_event(
            "old"
        )
        is False
    )


def test_rejected_events_do_not_change_cursor(
    tmp_path,
) -> None:
    """Equal and older events must not advance sequence."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="accepted",
            seq=100,
        )
    )

    processor.process(
        build_event(
            event_id="equal",
            seq=100,
        )
    )

    processor.process(
        build_event(
            event_id="older",
            seq=50,
        )
    )

    assert store.get_last_seq() == 100


def test_newer_sequence_is_accepted(
    tmp_path,
) -> None:
    """Strictly newer event should advance processing."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    result = processor.process(
        build_event(
            event_id="event-101",
            seq=101,
        )
    )

    assert result.status is EventProcessStatus.ACCEPTED
    assert store.get_last_seq() == 101


def test_sequence_cursor_survives_restart(
    tmp_path,
) -> None:
    """Durable ordering state must survive a BTS restart."""

    database_path = (
        tmp_path
        / "events.db"
    )

    first_store = EventStore(
        database_path
    )

    processor = EventProcessor(
        first_store
    )

    processor.process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    processor.process(
        build_event(
            event_id="event-101",
            seq=101,
        )
    )

    restarted_store = EventStore(
        database_path
    )

    assert restarted_store.get_last_seq() == 101


def test_processed_event_ids_survive_restart(
    tmp_path,
) -> None:
    """Idempotency state must remain durable."""

    database_path = (
        tmp_path
        / "events.db"
    )

    store = EventStore(
        database_path
    )

    EventProcessor(
        store
    ).process(
        build_event(
            event_id="durable-event",
            seq=100,
        )
    )

    restarted_store = EventStore(
        database_path
    )

    assert (
        restarted_store.has_processed_event(
            "durable-event"
        )
        is True
    )


def test_old_timestamp_with_newer_seq_is_currently_accepted(
    tmp_path,
) -> None:
    """Timestamp age is intentionally not yet an ordering rule."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    processor.process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    week_old = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            days=7
        )
    )

    result = processor.process(
        build_event(
            event_id="event-101-old-time",
            seq=101,
            timestamp=week_old,
        )
    )

    assert result.status is EventProcessStatus.ACCEPTED


def test_full_harness_passes(
    tmp_path,
) -> None:
    """Complete ordering scenario should succeed."""

    result = run_eagle_ordering_test(
        database_path=(
            tmp_path
            / "harness.db"
        )
    )

    assert result.successful is True


def test_success_result_is_successful() -> None:
    """Expected result should report success."""

    assert (
        create_success_result().successful
        is True
    )


def test_wrong_duplicate_status_fails_result() -> None:
    """Duplicate acceptance must invalidate result."""

    result = create_success_result()

    changed = EagleOrderingTestResult(
        first_status=result.first_status,
        duplicate_status=EventProcessStatus.ACCEPTED,
        equal_seq_status=result.equal_seq_status,
        older_seq_status=result.older_seq_status,
        cursor_after_rejections=result.cursor_after_rejections,
        equal_seq_event_persisted=result.equal_seq_event_persisted,
        older_seq_event_persisted=result.older_seq_event_persisted,
        newer_status=result.newer_status,
        cursor_after_newer=result.cursor_after_newer,
        restart_cursor=result.restart_cursor,
        first_event_survived_restart=(
            result.first_event_survived_restart
        ),
        newer_event_survived_restart=(
            result.newer_event_survived_restart
        ),
        older_timestamp_newer_seq_status=(
            result.older_timestamp_newer_seq_status
        ),
        final_cursor=result.final_cursor,
    )

    assert changed.successful is False


def test_wrong_final_cursor_fails_result() -> None:
    """Final durable cursor must reach 102."""

    result = create_success_result()

    changed = EagleOrderingTestResult(
        first_status=result.first_status,
        duplicate_status=result.duplicate_status,
        equal_seq_status=result.equal_seq_status,
        older_seq_status=result.older_seq_status,
        cursor_after_rejections=result.cursor_after_rejections,
        equal_seq_event_persisted=result.equal_seq_event_persisted,
        older_seq_event_persisted=result.older_seq_event_persisted,
        newer_status=result.newer_status,
        cursor_after_newer=result.cursor_after_newer,
        restart_cursor=result.restart_cursor,
        first_event_survived_restart=True,
        newer_event_survived_restart=True,
        older_timestamp_newer_seq_status=(
            EventProcessStatus.ACCEPTED
        ),
        final_cursor=101,
    )

    assert changed.successful is False


def test_require_status_rejects_wrong_status(
    tmp_path,
) -> None:
    """Harness helper should fail on unexpected processing result."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    result = EventProcessor(
        store
    ).process(
        build_event(
            event_id="event-100",
            seq=100,
        )
    )

    with pytest.raises(
        RuntimeError,
        match="Unexpected Eagle",
    ):
        require_status(
            result,
            EventProcessStatus.OUT_OF_SEQUENCE,
        )


def test_result_is_immutable() -> None:
    """Ordering result must not mutate."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_cursor = 999  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful ordering result should print PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "DuplicateEvent" in output
    assert "OutOfSequence" in output


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )