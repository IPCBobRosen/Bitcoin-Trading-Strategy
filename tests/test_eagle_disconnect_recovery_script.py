"""Tests for the offline Eagle disconnect/recovery harness."""

from dataclasses import FrozenInstanceError

import pytest

from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.replay_tracker import ReplayTracker
from app.resume_manager import ResumeStatus

from scripts.test_eagle_disconnect_recovery import (
    PRE_DISCONNECT_SEQ,
    REPLAY_SEQ_1,
    REPLAY_SEQ_2,
    EagleDisconnectRecoveryResult,
    build_lifecycle_event,
    build_reconnect_hello,
    print_result,
    process_replay_event,
    run_eagle_disconnect_recovery_test,
    seed_pre_disconnect_state,
)


def create_success_result(
) -> EagleDisconnectRecoveryResult:
    """Create one completely successful recovery result."""

    return EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=(
            "wss://example.com/ipc/v1/stream?"
            "since_seq=100"
        ),
        ready_before_hello=False,
        resumed_before_hello=False,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=(
            EventProcessStatus.DUPLICATE_EVENT
        ),
        out_of_sequence_status=(
            EventProcessStatus.OUT_OF_SEQUENCE
        ),
        cursor_after_rejections=100,
        first_replay_status=(
            EventProcessStatus.ACCEPTED
        ),
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=(
            EventProcessStatus.ACCEPTED
        ),
        replay_complete_after_second=True,
        cursor_after_replay=102,
        ready_before_reconciliation=False,
        reconciliation_matched=True,
        ready_before_heartbeat=False,
        heartbeat_healthy=True,
        final_readiness=True,
        resume_status=ResumeStatus.RESUMED,
        trading_paused_final=False,
    )


def test_sequence_constants_are_consecutive() -> None:
    """Harness should exercise durable replay from 100 to 102."""

    assert PRE_DISCONNECT_SEQ == 100
    assert REPLAY_SEQ_1 == 101
    assert REPLAY_SEQ_2 == 102


def test_build_lifecycle_event() -> None:
    """Replay helper should build validated lifecycle event."""

    event = build_lifecycle_event(
        event_id="event-101",
        seq=101,
    )

    assert event.event_id == "event-101"
    assert event.seq == 101
    assert event.message_type == "fund.entry"
    assert event.payload["intent"] == "BUY_TO_OPEN"


def test_reconnect_hello_announces_expected_replay() -> None:
    """Hello must echo durable cursor and announce two events."""

    hello = build_reconnect_hello()

    assert hello.since_seq == 100
    assert hello.last_seq == 102
    assert hello.replay_count == 2
    assert hello.open_count == 0


def test_seed_pre_disconnect_state_sets_cursor(
    tmp_path,
) -> None:
    """Seed helper should establish durable cursor 100."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    seed_pre_disconnect_state(
        processor
    )

    assert store.get_last_seq() == 100

    assert store.has_processed_event(
        "pre-disconnect-event-100"
    )


def test_duplicate_replay_is_not_counted(
    tmp_path,
) -> None:
    """Duplicate replay must not advance replay progress."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    seed_pre_disconnect_state(
        processor
    )

    tracker = ReplayTracker()

    tracker.process_hello(
        build_reconnect_hello()
    )

    duplicate = build_lifecycle_event(
        event_id="pre-disconnect-event-100",
        seq=101,
    )

    status = process_replay_event(
        processor=processor,
        replay_tracker=tracker,
        event=duplicate,
    )

    assert (
        status
        is EventProcessStatus.DUPLICATE_EVENT
    )

    assert (
        tracker.processed_replay_count
        == 0
    )

    assert tracker.replay_complete is False

    assert store.get_last_seq() == 100


def test_out_of_sequence_replay_is_not_counted(
    tmp_path,
) -> None:
    """Old replay event must not satisfy replay count."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    seed_pre_disconnect_state(
        processor
    )

    tracker = ReplayTracker()

    tracker.process_hello(
        build_reconnect_hello()
    )

    old_event = build_lifecycle_event(
        event_id="old-event",
        seq=100,
    )

    status = process_replay_event(
        processor=processor,
        replay_tracker=tracker,
        event=old_event,
    )

    assert (
        status
        is EventProcessStatus.OUT_OF_SEQUENCE
    )

    assert (
        tracker.processed_replay_count
        == 0
    )

    assert store.get_last_seq() == 100


def test_accepted_replay_is_counted(
    tmp_path,
) -> None:
    """Accepted replay event should advance both states."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    seed_pre_disconnect_state(
        processor
    )

    tracker = ReplayTracker()

    tracker.process_hello(
        build_reconnect_hello()
    )

    event = build_lifecycle_event(
        event_id="replay-101",
        seq=101,
    )

    status = process_replay_event(
        processor=processor,
        replay_tracker=tracker,
        event=event,
    )

    assert (
        status
        is EventProcessStatus.ACCEPTED
    )

    assert (
        tracker.processed_replay_count
        == 1
    )

    assert tracker.replay_complete is False
    assert store.get_last_seq() == 101


def test_two_accepted_replay_events_complete_replay(
    tmp_path,
) -> None:
    """Replay should complete only after both announced events."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        store
    )

    seed_pre_disconnect_state(
        processor
    )

    tracker = ReplayTracker()

    tracker.process_hello(
        build_reconnect_hello()
    )

    process_replay_event(
        processor=processor,
        replay_tracker=tracker,
        event=build_lifecycle_event(
            event_id="replay-101",
            seq=101,
        ),
    )

    process_replay_event(
        processor=processor,
        replay_tracker=tracker,
        event=build_lifecycle_event(
            event_id="replay-102",
            seq=102,
        ),
    )

    assert (
        tracker.processed_replay_count
        == 2
    )

    assert tracker.replay_complete is True
    assert store.get_last_seq() == 102


def test_full_disconnect_recovery_harness_passes(
    tmp_path,
) -> None:
    """Full reconnect safety scenario should pass."""

    result = (
        run_eagle_disconnect_recovery_test(
            database_path=(
                tmp_path
                / "events.db"
            )
        )
    )

    assert result.successful is True


def test_success_result_reports_success() -> None:
    """Canonical successful result must be successful."""

    assert (
        create_success_result().successful
        is True
    )


def test_reconnect_without_since_seq_fails_success() -> None:
    """Reconnect must use durable Eagle sequence cursor."""

    result = create_success_result()

    changed = EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=(
            "wss://example.com/ipc/v1/stream"
        ),
        ready_before_hello=False,
        resumed_before_hello=False,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=result.duplicate_status,
        out_of_sequence_status=(
            result.out_of_sequence_status
        ),
        cursor_after_rejections=100,
        first_replay_status=result.first_replay_status,
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=result.second_replay_status,
        replay_complete_after_second=True,
        cursor_after_replay=102,
        ready_before_reconciliation=False,
        reconciliation_matched=True,
        ready_before_heartbeat=False,
        heartbeat_healthy=True,
        final_readiness=True,
        resume_status=ResumeStatus.RESUMED,
        trading_paused_final=False,
    )

    assert changed.successful is False


def test_premature_resume_fails_success() -> None:
    """Trading may not resume before reconnect prerequisites."""

    result = create_success_result()

    changed = EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=result.reconnect_uri,
        ready_before_hello=False,
        resumed_before_hello=True,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=result.duplicate_status,
        out_of_sequence_status=(
            result.out_of_sequence_status
        ),
        cursor_after_rejections=100,
        first_replay_status=result.first_replay_status,
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=result.second_replay_status,
        replay_complete_after_second=True,
        cursor_after_replay=102,
        ready_before_reconciliation=False,
        reconciliation_matched=True,
        ready_before_heartbeat=False,
        heartbeat_healthy=True,
        final_readiness=True,
        resume_status=ResumeStatus.RESUMED,
        trading_paused_final=False,
    )

    assert changed.successful is False


def test_duplicate_acceptance_fails_success() -> None:
    """Duplicate replay must never be considered accepted."""

    result = create_success_result()

    changed = EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=result.reconnect_uri,
        ready_before_hello=False,
        resumed_before_hello=False,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=(
            EventProcessStatus.ACCEPTED
        ),
        out_of_sequence_status=(
            result.out_of_sequence_status
        ),
        cursor_after_rejections=100,
        first_replay_status=result.first_replay_status,
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=result.second_replay_status,
        replay_complete_after_second=True,
        cursor_after_replay=102,
        ready_before_reconciliation=False,
        reconciliation_matched=True,
        ready_before_heartbeat=False,
        heartbeat_healthy=True,
        final_readiness=True,
        resume_status=ResumeStatus.RESUMED,
        trading_paused_final=False,
    )

    assert changed.successful is False


def test_incomplete_replay_fails_success() -> None:
    """Reconnect must not succeed while replay remains incomplete."""

    result = create_success_result()

    changed = EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=result.reconnect_uri,
        ready_before_hello=False,
        resumed_before_hello=False,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=result.duplicate_status,
        out_of_sequence_status=(
            result.out_of_sequence_status
        ),
        cursor_after_rejections=100,
        first_replay_status=result.first_replay_status,
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=result.second_replay_status,
        replay_complete_after_second=False,
        cursor_after_replay=102,
        ready_before_reconciliation=False,
        reconciliation_matched=True,
        ready_before_heartbeat=False,
        heartbeat_healthy=True,
        final_readiness=True,
        resume_status=ResumeStatus.RESUMED,
        trading_paused_final=False,
    )

    assert changed.successful is False


def test_unmatched_reconciliation_fails_success() -> None:
    """Position mismatch must prevent successful recovery."""

    result = create_success_result()

    changed = EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=result.reconnect_uri,
        ready_before_hello=False,
        resumed_before_hello=False,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=result.duplicate_status,
        out_of_sequence_status=(
            result.out_of_sequence_status
        ),
        cursor_after_rejections=100,
        first_replay_status=result.first_replay_status,
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=result.second_replay_status,
        replay_complete_after_second=True,
        cursor_after_replay=102,
        ready_before_reconciliation=False,
        reconciliation_matched=False,
        ready_before_heartbeat=False,
        heartbeat_healthy=True,
        final_readiness=True,
        resume_status=ResumeStatus.RESUMED,
        trading_paused_final=False,
    )

    assert changed.successful is False


def test_unhealthy_heartbeat_fails_success() -> None:
    """Fresh Eagle heartbeat is required before resume."""

    result = create_success_result()

    changed = EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=result.reconnect_uri,
        ready_before_hello=False,
        resumed_before_hello=False,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=result.duplicate_status,
        out_of_sequence_status=(
            result.out_of_sequence_status
        ),
        cursor_after_rejections=100,
        first_replay_status=result.first_replay_status,
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=result.second_replay_status,
        replay_complete_after_second=True,
        cursor_after_replay=102,
        ready_before_reconciliation=False,
        reconciliation_matched=True,
        ready_before_heartbeat=False,
        heartbeat_healthy=False,
        final_readiness=False,
        resume_status=ResumeStatus.REJECTED,
        trading_paused_final=True,
    )

    assert changed.successful is False


def test_final_cursor_must_be_102() -> None:
    """Successful replay must durably advance through seq 102."""

    result = create_success_result()

    changed = EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=100,
        reconnect_uri=result.reconnect_uri,
        ready_before_hello=False,
        resumed_before_hello=False,
        hello_received=True,
        announced_replay_count=2,
        ready_after_hello=False,
        duplicate_status=result.duplicate_status,
        out_of_sequence_status=(
            result.out_of_sequence_status
        ),
        cursor_after_rejections=100,
        first_replay_status=result.first_replay_status,
        replay_complete_after_first=False,
        ready_after_first_replay=False,
        second_replay_status=result.second_replay_status,
        replay_complete_after_second=True,
        cursor_after_replay=101,
        ready_before_reconciliation=False,
        reconciliation_matched=True,
        ready_before_heartbeat=False,
        heartbeat_healthy=True,
        final_readiness=True,
        resume_status=ResumeStatus.RESUMED,
        trading_paused_final=False,
    )

    assert changed.successful is False


def test_result_is_immutable() -> None:
    """Recovery result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_readiness = False  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful recovery should print PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "DuplicateEvent" in output
    assert "OutOfSequence" in output
    assert "Resumed" in output


def test_print_result_requires_correct_type() -> None:
    """Printer must reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )