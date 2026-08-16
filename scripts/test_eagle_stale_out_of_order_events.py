"""Offline Eagle stale/out-of-order event safety harness.

This harness exercises the production EventProcessor and EventStore.

It proves:

1. A first new event with a new sequence is accepted.
2. Replaying the same event_id is rejected as a duplicate.
3. A new event with the same sequence is out of sequence.
4. A new event with an older sequence is out of sequence.
5. Rejected events do not advance the durable sequence cursor.
6. Rejected out-of-sequence event IDs are not persisted as processed.
7. A newer sequence is accepted.
8. Event and sequence state survive EventStore restart.
9. Timestamp age alone is NOT currently a rejection criterion.

No IB or broker execution path exists in this harness.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import (
    EventProcessResult,
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore


INITIAL_SEQ = 100
NEWER_SEQ = 101
OLDER_TIMESTAMP_SEQ = 102


@dataclass(frozen=True, slots=True)
class EagleOrderingTestResult:
    """Immutable result of the Eagle ordering safety test."""

    first_status: EventProcessStatus
    duplicate_status: EventProcessStatus
    equal_seq_status: EventProcessStatus
    older_seq_status: EventProcessStatus

    cursor_after_rejections: int | None

    equal_seq_event_persisted: bool
    older_seq_event_persisted: bool

    newer_status: EventProcessStatus
    cursor_after_newer: int | None

    restart_cursor: int | None
    first_event_survived_restart: bool
    newer_event_survived_restart: bool

    older_timestamp_newer_seq_status: EventProcessStatus
    final_cursor: int | None

    @property
    def successful(self) -> bool:
        """Return True when every ordering protection behaved correctly."""

        return (
            self.first_status
            is EventProcessStatus.ACCEPTED
            and self.duplicate_status
            is EventProcessStatus.DUPLICATE_EVENT
            and self.equal_seq_status
            is EventProcessStatus.OUT_OF_SEQUENCE
            and self.older_seq_status
            is EventProcessStatus.OUT_OF_SEQUENCE
            and self.cursor_after_rejections == INITIAL_SEQ
            and not self.equal_seq_event_persisted
            and not self.older_seq_event_persisted
            and self.newer_status
            is EventProcessStatus.ACCEPTED
            and self.cursor_after_newer == NEWER_SEQ
            and self.restart_cursor == NEWER_SEQ
            and self.first_event_survived_restart
            and self.newer_event_survived_restart
            and self.older_timestamp_newer_seq_status
            is EventProcessStatus.ACCEPTED
            and self.final_cursor == OLDER_TIMESTAMP_SEQ
        )


def build_event(
    *,
    event_id: str,
    seq: int,
    signal_id: str = "eagle-ordering-signal-001",
    timestamp: datetime | None = None,
    intent: str = "BUY_TO_OPEN",
) -> IncomingLifecycleEvent:
    """Build one validated Eagle lifecycle event."""

    if timestamp is None:
        timestamp = datetime.now(
            timezone.utc
        )

    if timestamp.tzinfo is None:
        raise ValueError(
            "'timestamp' must be timezone-aware."
        )

    message = {
        "type": "trade.lifecycle",
        "seq": seq,
        "event_id": event_id,
        "signal_id": signal_id,
        "ts": timestamp.isoformat(),
        "env": "staging",
        "payload": {
            "intent": intent,
        },
    }

    return IncomingLifecycleEvent.from_dict(
        message
    )


def require_status(
    result: EventProcessResult,
    expected: EventProcessStatus,
) -> EventProcessStatus:
    """Require one exact EventProcessor outcome."""

    if not isinstance(
        result,
        EventProcessResult,
    ):
        raise TypeError(
            "'result' must be an EventProcessResult."
        )

    if not isinstance(
        expected,
        EventProcessStatus,
    ):
        raise TypeError(
            "'expected' must be an EventProcessStatus."
        )

    if result.status is not expected:
        raise RuntimeError(
            "Unexpected Eagle event-processing result. "
            f"Expected {expected.value}, "
            f"observed {result.status.value}."
        )

    return result.status


def run_eagle_ordering_test(
    *,
    database_path: str | Path,
) -> EagleOrderingTestResult:
    """Run durable Eagle duplicate/sequence validation."""

    store = EventStore(
        database_path
    )

    processor = EventProcessor(
        store
    )

    first_event = build_event(
        event_id="ordering-event-100",
        seq=INITIAL_SEQ,
    )

    first_status = require_status(
        processor.process(
            first_event
        ),
        EventProcessStatus.ACCEPTED,
    )

    if store.get_last_seq() != INITIAL_SEQ:
        raise RuntimeError(
            "Initial accepted event did not establish "
            "the expected sequence cursor."
        )

    # ---------------------------------------------------------
    # Same event ID again, even with a newer sequence.
    # Event identity must win and cursor must not advance.
    # ---------------------------------------------------------

    duplicate_event = build_event(
        event_id=first_event.event_id,
        seq=105,
    )

    duplicate_status = require_status(
        processor.process(
            duplicate_event
        ),
        EventProcessStatus.DUPLICATE_EVENT,
    )

    # ---------------------------------------------------------
    # Different event ID, same sequence.
    # ---------------------------------------------------------

    equal_seq_event = build_event(
        event_id="ordering-event-equal-100",
        seq=INITIAL_SEQ,
    )

    equal_seq_status = require_status(
        processor.process(
            equal_seq_event
        ),
        EventProcessStatus.OUT_OF_SEQUENCE,
    )

    # ---------------------------------------------------------
    # Different event ID, older sequence.
    # ---------------------------------------------------------

    older_seq_event = build_event(
        event_id="ordering-event-099",
        seq=INITIAL_SEQ - 1,
    )

    older_seq_status = require_status(
        processor.process(
            older_seq_event
        ),
        EventProcessStatus.OUT_OF_SEQUENCE,
    )

    cursor_after_rejections = (
        store.get_last_seq()
    )

    if cursor_after_rejections != INITIAL_SEQ:
        raise RuntimeError(
            "Rejected events improperly changed "
            "the durable sequence cursor."
        )

    equal_seq_event_persisted = (
        store.has_processed_event(
            equal_seq_event.event_id
        )
    )

    older_seq_event_persisted = (
        store.has_processed_event(
            older_seq_event.event_id
        )
    )

    if equal_seq_event_persisted:
        raise RuntimeError(
            "Equal-sequence rejected event was "
            "incorrectly persisted as processed."
        )

    if older_seq_event_persisted:
        raise RuntimeError(
            "Older-sequence rejected event was "
            "incorrectly persisted as processed."
        )

    # ---------------------------------------------------------
    # Newer sequence should proceed normally.
    # ---------------------------------------------------------

    newer_event = build_event(
        event_id="ordering-event-101",
        seq=NEWER_SEQ,
    )

    newer_status = require_status(
        processor.process(
            newer_event
        ),
        EventProcessStatus.ACCEPTED,
    )

    cursor_after_newer = (
        store.get_last_seq()
    )

    if cursor_after_newer != NEWER_SEQ:
        raise RuntimeError(
            "Accepted newer event did not advance "
            "the durable sequence cursor."
        )

    # ---------------------------------------------------------
    # Simulate BTS restart by opening a new EventStore object
    # against the same SQLite database.
    # ---------------------------------------------------------

    restarted_store = EventStore(
        database_path
    )

    restart_cursor = (
        restarted_store.get_last_seq()
    )

    first_event_survived_restart = (
        restarted_store.has_processed_event(
            first_event.event_id
        )
    )

    newer_event_survived_restart = (
        restarted_store.has_processed_event(
            newer_event.event_id
        )
    )

    if restart_cursor != NEWER_SEQ:
        raise RuntimeError(
            "Durable sequence cursor did not survive restart."
        )

    if not first_event_survived_restart:
        raise RuntimeError(
            "Original processed event did not survive restart."
        )

    if not newer_event_survived_restart:
        raise RuntimeError(
            "Newer processed event did not survive restart."
        )

    restarted_processor = EventProcessor(
        restarted_store
    )

    # ---------------------------------------------------------
    # Important current-policy test:
    #
    # The timestamp is deliberately old, but seq=102 is newer.
    # Existing production logic should ACCEPT this because
    # timestamp freshness is not yet an EventProcessor rule.
    #
    # This is intentional and protects reconnect replay semantics.
    # ---------------------------------------------------------

    old_timestamp = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            days=7
        )
    )

    older_timestamp_event = build_event(
        event_id="ordering-event-102-old-ts",
        seq=OLDER_TIMESTAMP_SEQ,
        timestamp=old_timestamp,
    )

    older_timestamp_newer_seq_status = (
        require_status(
            restarted_processor.process(
                older_timestamp_event
            ),
            EventProcessStatus.ACCEPTED,
        )
    )

    final_cursor = (
        restarted_store.get_last_seq()
    )

    return EagleOrderingTestResult(
        first_status=first_status,
        duplicate_status=duplicate_status,
        equal_seq_status=equal_seq_status,
        older_seq_status=older_seq_status,

        cursor_after_rejections=(
            cursor_after_rejections
        ),

        equal_seq_event_persisted=(
            equal_seq_event_persisted
        ),
        older_seq_event_persisted=(
            older_seq_event_persisted
        ),

        newer_status=newer_status,
        cursor_after_newer=(
            cursor_after_newer
        ),

        restart_cursor=restart_cursor,
        first_event_survived_restart=(
            first_event_survived_restart
        ),
        newer_event_survived_restart=(
            newer_event_survived_restart
        ),

        older_timestamp_newer_seq_status=(
            older_timestamp_newer_seq_status
        ),
        final_cursor=final_cursor,
    )


def print_result(
    result: EagleOrderingTestResult,
) -> None:
    """Print Eagle sequence/order safety results."""

    if not isinstance(
        result,
        EagleOrderingTestResult,
    ):
        raise TypeError(
            "'result' must be an EagleOrderingTestResult."
        )

    print()
    print(
        "BTS / EAGLE STALE & OUT-OF-ORDER EVENT TEST"
    )
    print(
        "================================================"
    )
    print(
        f"First event:                    "
        f"{result.first_status.value}"
    )
    print(
        f"Duplicate event:                "
        f"{result.duplicate_status.value}"
    )
    print(
        f"Equal sequence event:           "
        f"{result.equal_seq_status.value}"
    )
    print(
        f"Older sequence event:           "
        f"{result.older_seq_status.value}"
    )
    print(
        f"Cursor after rejections:        "
        f"{result.cursor_after_rejections}"
    )
    print(
        f"Equal-seq event persisted:      "
        f"{result.equal_seq_event_persisted}"
    )
    print(
        f"Older-seq event persisted:      "
        f"{result.older_seq_event_persisted}"
    )
    print(
        f"Newer event:                    "
        f"{result.newer_status.value}"
    )
    print(
        f"Cursor after newer event:       "
        f"{result.cursor_after_newer}"
    )
    print(
        f"Restart cursor:                 "
        f"{result.restart_cursor}"
    )
    print(
        f"First event survived restart:   "
        f"{result.first_event_survived_restart}"
    )
    print(
        f"Newer event survived restart:   "
        f"{result.newer_event_survived_restart}"
    )
    print(
        "Old timestamp + newer seq:      "
        f"{result.older_timestamp_newer_seq_status.value}"
    )
    print(
        f"Final sequence cursor:          "
        f"{result.final_cursor}"
    )
    print(
        "================================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - Eagle duplicate and sequence "
            "ordering remained fail-safe."
        )
    else:
        print(
            "RESULT: FAIL - Eagle ordering validation failed."
        )

    print()


def main() -> int:
    """Run the offline Eagle ordering test."""

    print()
    print(
        "Starting offline Eagle stale/out-of-order test..."
    )
    print(
        "No IB connection or broker-order path exists."
    )

    with TemporaryDirectory(
        prefix="bts_eagle_ordering_"
    ) as temporary_directory:

        database_path = (
            Path(
                temporary_directory
            )
            / "event_store.db"
        )

        try:
            result = run_eagle_ordering_test(
                database_path=database_path
            )

        except Exception as error:
            print()
            print(
                "RESULT: FAIL"
            )
            print(
                f"{type(error).__name__}: {error}"
            )
            print()

            return 1

    print_result(
        result
    )

    return (
        0
        if result.successful
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )