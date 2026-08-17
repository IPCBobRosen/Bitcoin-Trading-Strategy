"""Offline Eagle disconnect/reconnect safety harness.

This harness proves BTS remains fail-closed after an Eagle
connection interruption until every reconnect prerequisite has
been satisfied.

Sequence:

    durable Eagle cursor = 100
        ↓
    trading PAUSED
        ↓
    reconnect requested with since_seq=100
        ↓
    fund.hello announces:
        since_seq=100
        last_seq=102
        replay_count=2
        ↓
    reconnect alone does NOT resume trading
        ↓
    duplicate replay event rejected
    out-of-sequence replay event rejected
        ↓
    accepted replay seq=101
        ↓
    replay still incomplete
        ↓
    accepted replay seq=102
        ↓
    replay complete
        ↓
    reconciliation still required
        ↓
    reconciliation MATCHED
        ↓
    heartbeat still required
        ↓
    fresh heartbeat recorded
        ↓
    reconnect readiness = READY
        ↓
    ResumeManager resumes trading
        ↓
    durable cursor = 102
        ↓
    PASS

No IB connection, IB order factory, or broker-submission path
exists in this harness.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.communications.eagle_client import EagleClient
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.connection_health import ConnectionHealth
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.reconciliation_manager import ReconciliationManager
from app.reconnect_readiness import ReconnectReadiness
from app.replay_tracker import ReplayTracker
from app.resume_manager import (
    ResumeManager,
    ResumeStatus,
)
from app.trading_controls import TradingControls


PRE_DISCONNECT_SEQ = 100
REPLAY_SEQ_1 = 101
REPLAY_SEQ_2 = 102


@dataclass(frozen=True, slots=True)
class EagleDisconnectRecoveryResult:
    """Immutable result of the Eagle reconnect safety test."""

    durable_cursor_before_reconnect: int | None
    reconnect_uri: str

    ready_before_hello: bool
    resumed_before_hello: bool

    hello_received: bool
    announced_replay_count: int
    ready_after_hello: bool

    duplicate_status: EventProcessStatus
    out_of_sequence_status: EventProcessStatus
    cursor_after_rejections: int | None

    first_replay_status: EventProcessStatus
    replay_complete_after_first: bool
    ready_after_first_replay: bool

    second_replay_status: EventProcessStatus
    replay_complete_after_second: bool
    cursor_after_replay: int | None

    ready_before_reconciliation: bool
    reconciliation_matched: bool
    ready_before_heartbeat: bool

    heartbeat_healthy: bool
    final_readiness: bool
    resume_status: ResumeStatus
    trading_paused_final: bool

    @property
    def successful(self) -> bool:
        """Return True when reconnect remained fail-closed."""

        return (
            self.durable_cursor_before_reconnect
            == PRE_DISCONNECT_SEQ
            and (
                "since_seq=100"
                in self.reconnect_uri
            )
            and not self.ready_before_hello
            and not self.resumed_before_hello
            and self.hello_received
            and self.announced_replay_count == 2
            and not self.ready_after_hello
            and (
                self.duplicate_status
                is EventProcessStatus.DUPLICATE_EVENT
            )
            and (
                self.out_of_sequence_status
                is EventProcessStatus.OUT_OF_SEQUENCE
            )
            and (
                self.cursor_after_rejections
                == PRE_DISCONNECT_SEQ
            )
            and (
                self.first_replay_status
                is EventProcessStatus.ACCEPTED
            )
            and not self.replay_complete_after_first
            and not self.ready_after_first_replay
            and (
                self.second_replay_status
                is EventProcessStatus.ACCEPTED
            )
            and self.replay_complete_after_second
            and self.cursor_after_replay == REPLAY_SEQ_2
            and not self.ready_before_reconciliation
            and self.reconciliation_matched
            and not self.ready_before_heartbeat
            and self.heartbeat_healthy
            and self.final_readiness
            and (
                self.resume_status
                is ResumeStatus.RESUMED
            )
            and not self.trading_paused_final
        )


def build_lifecycle_event(
    *,
    event_id: str,
    seq: int,
    signal_id: str = "reconnect-signal-001",
) -> IncomingLifecycleEvent:
    """Create one validated Eagle replay lifecycle event."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": seq,
            "event_id": event_id,
            "signal_id": signal_id,
            "ts": "2026-08-17T00:00:00+00:00",
            "env": "staging",
            "payload": {
                "intent": "BUY_TO_OPEN",
            },
        }
    )


def build_reconnect_hello() -> EagleHello:
    """Create Eagle hello announcing two replay events."""

    return EagleHello.from_dict(
        {
            "type": "fund.hello",
            "contract": "1.2.0",
            "version": "1.2.0",
            "capabilities": [
                "size_mult",
                "fund.add",
                "funding_rate",
                "p14_disable",
            ],
            "flags": {
                "p14_disabled": True,
                "size_mult_enabled": False,
                "pyramid_enabled": False,
            },
            "last_seq": REPLAY_SEQ_2,
            "since_seq": PRE_DISCONNECT_SEQ,
            "open_count": 0,
            "open": [],
            "replay_count": 2,
            "ts": "2026-08-17T00:00:01+00:00",
            "env": "staging",
        }
    )


def seed_pre_disconnect_state(
    processor: EventProcessor,
) -> None:
    """Persist sequence 100 as BTS state before disconnect."""

    result = processor.process(
        build_lifecycle_event(
            event_id="pre-disconnect-event-100",
            seq=PRE_DISCONNECT_SEQ,
        )
    )

    if (
        result.status
        is not EventProcessStatus.ACCEPTED
    ):
        raise RuntimeError(
            "Could not establish pre-disconnect "
            "Eagle sequence state."
        )


def process_replay_event(
    *,
    processor: EventProcessor,
    replay_tracker: ReplayTracker,
    event: IncomingLifecycleEvent,
) -> EventProcessStatus:
    """Process replay event and count only accepted replay."""

    result = processor.process(
        event
    )

    if (
        result.status
        is EventProcessStatus.ACCEPTED
    ):
        replay_tracker.record_lifecycle_event(
            event
        )

    return result.status


def run_eagle_disconnect_recovery_test(
    *,
    database_path: str | Path,
) -> EagleDisconnectRecoveryResult:
    """Run complete offline Eagle reconnect safety scenario."""

    event_store = EventStore(
        database_path
    )

    event_processor = EventProcessor(
        event_store
    )

    seed_pre_disconnect_state(
        event_processor
    )

    durable_cursor_before_reconnect = (
        event_store.get_last_seq()
    )

    if (
        durable_cursor_before_reconnect
        != PRE_DISCONNECT_SEQ
    ):
        raise RuntimeError(
            "Unexpected pre-disconnect durable cursor."
        )

    eagle_client = EagleClient(
        "wss://example.com/ipc/v1/stream",
        api_key="offline-test-key",
        since_seq=(
            durable_cursor_before_reconnect
        ),
    )

    reconnect_uri = (
        eagle_client._connection_uri()
    )

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    controls.pause()

    replay_tracker = ReplayTracker()
    reconciliation_manager = (
        ReconciliationManager()
    )
    connection_health = ConnectionHealth(
        heartbeat_timeout_seconds=45
    )

    reconnect_readiness = (
        ReconnectReadiness(
            replay_tracker,
            reconciliation_manager,
            connection_health,
        )
    )

    resume_manager = ResumeManager(
        controls,
        reconnect_readiness,
    )

    # ---------------------------------------------------------
    # Reconnect alone must not permit trading.
    # ---------------------------------------------------------

    ready_before_hello = (
        reconnect_readiness.evaluate().ready
    )

    premature_resume = (
        resume_manager.request_resume()
    )

    resumed_before_hello = (
        premature_resume.resumed
    )

    if resumed_before_hello:
        raise RuntimeError(
            "Safety violation: trading resumed "
            "before fund.hello."
        )

    if not controls.is_paused:
        raise RuntimeError(
            "Trading was not left paused after "
            "premature resume request."
        )

    # ---------------------------------------------------------
    # Eagle hello begins replay tracking.
    # ---------------------------------------------------------

    hello = build_reconnect_hello()

    replay_tracker.process_hello(
        hello
    )

    ready_after_hello = (
        reconnect_readiness.evaluate().ready
    )

    if ready_after_hello:
        raise RuntimeError(
            "Safety violation: fund.hello alone "
            "made reconnect ready."
        )

    # ---------------------------------------------------------
    # Duplicate replay:
    #
    # Same event ID as the pre-disconnect durable event,
    # but presented with a newer sequence.
    #
    # It must NOT advance the durable cursor and must NOT count
    # toward replay completion.
    # ---------------------------------------------------------

    duplicate_event = (
        build_lifecycle_event(
            event_id="pre-disconnect-event-100",
            seq=REPLAY_SEQ_1,
        )
    )

    duplicate_status = (
        process_replay_event(
            processor=event_processor,
            replay_tracker=replay_tracker,
            event=duplicate_event,
        )
    )

    if (
        duplicate_status
        is not EventProcessStatus.DUPLICATE_EVENT
    ):
        raise RuntimeError(
            "Expected duplicate replay event rejection."
        )

    # ---------------------------------------------------------
    # New event with old/equal sequence:
    # must also be rejected and not count toward replay.
    # ---------------------------------------------------------

    old_sequence_event = (
        build_lifecycle_event(
            event_id="new-event-old-sequence",
            seq=PRE_DISCONNECT_SEQ,
        )
    )

    out_of_sequence_status = (
        process_replay_event(
            processor=event_processor,
            replay_tracker=replay_tracker,
            event=old_sequence_event,
        )
    )

    if (
        out_of_sequence_status
        is not EventProcessStatus.OUT_OF_SEQUENCE
    ):
        raise RuntimeError(
            "Expected out-of-sequence replay rejection."
        )

    cursor_after_rejections = (
        event_store.get_last_seq()
    )

    if (
        cursor_after_rejections
        != PRE_DISCONNECT_SEQ
    ):
        raise RuntimeError(
            "Rejected replay events changed "
            "the durable cursor."
        )

    if (
        replay_tracker.processed_replay_count
        != 0
    ):
        raise RuntimeError(
            "Rejected replay events incorrectly "
            "counted toward replay completion."
        )

    # ---------------------------------------------------------
    # First legitimate replay event.
    # ---------------------------------------------------------

    first_replay_event = (
        build_lifecycle_event(
            event_id="replay-event-101",
            seq=REPLAY_SEQ_1,
        )
    )

    first_replay_status = (
        process_replay_event(
            processor=event_processor,
            replay_tracker=replay_tracker,
            event=first_replay_event,
        )
    )

    replay_complete_after_first = (
        replay_tracker.replay_complete
    )

    ready_after_first_replay = (
        reconnect_readiness.evaluate().ready
    )

    if replay_complete_after_first:
        raise RuntimeError(
            "Replay completed after only one "
            "of two announced events."
        )

    if ready_after_first_replay:
        raise RuntimeError(
            "Reconnect became ready before replay completed."
        )

    # ---------------------------------------------------------
    # Second legitimate replay event.
    # ---------------------------------------------------------

    second_replay_event = (
        build_lifecycle_event(
            event_id="replay-event-102",
            seq=REPLAY_SEQ_2,
        )
    )

    second_replay_status = (
        process_replay_event(
            processor=event_processor,
            replay_tracker=replay_tracker,
            event=second_replay_event,
        )
    )

    replay_complete_after_second = (
        replay_tracker.replay_complete
    )

    cursor_after_replay = (
        event_store.get_last_seq()
    )

    if not replay_complete_after_second:
        raise RuntimeError(
            "Replay did not complete after two "
            "accepted replay events."
        )

    # ---------------------------------------------------------
    # Even complete replay is not enough.
    # Reconciliation has not yet occurred.
    # ---------------------------------------------------------

    ready_before_reconciliation = (
        reconnect_readiness.evaluate().ready
    )

    if ready_before_reconciliation:
        raise RuntimeError(
            "Reconnect became ready before reconciliation."
        )

    reconciliation_result = (
        reconciliation_manager.reconcile(
            eagle_positions=[],
            broker_positions=[],
        )
    )

    reconciliation_matched = (
        reconciliation_result.matched
    )

    if not reconciliation_matched:
        raise RuntimeError(
            "Expected flat Eagle/broker snapshots to match."
        )

    # ---------------------------------------------------------
    # Reconciliation alone is still insufficient.
    # A healthy heartbeat is required.
    # ---------------------------------------------------------

    ready_before_heartbeat = (
        reconnect_readiness.evaluate().ready
    )

    if ready_before_heartbeat:
        raise RuntimeError(
            "Reconnect became ready before heartbeat."
        )

    connection_health.record_heartbeat(
        received_at=datetime.now(
            timezone.utc
        )
    )

    heartbeat_healthy = (
        connection_health.is_healthy()
    )

    if not heartbeat_healthy:
        raise RuntimeError(
            "Fresh Eagle heartbeat was not healthy."
        )

    # ---------------------------------------------------------
    # All prerequisites are now satisfied.
    # ---------------------------------------------------------

    final_readiness = (
        reconnect_readiness.evaluate().ready
    )

    if not final_readiness:
        raise RuntimeError(
            "Reconnect did not become ready after "
            "all safety prerequisites were satisfied."
        )

    resume_result = (
        resume_manager.request_resume()
    )

    if (
        resume_result.status
        is not ResumeStatus.RESUMED
    ):
        raise RuntimeError(
            "Trading did not resume after reconnect "
            "became fully ready."
        )

    return EagleDisconnectRecoveryResult(
        durable_cursor_before_reconnect=(
            durable_cursor_before_reconnect
        ),
        reconnect_uri=reconnect_uri,

        ready_before_hello=(
            ready_before_hello
        ),
        resumed_before_hello=(
            resumed_before_hello
        ),

        hello_received=(
            replay_tracker.hello_received
        ),
        announced_replay_count=(
            replay_tracker.expected_replay_count
        ),
        ready_after_hello=(
            ready_after_hello
        ),

        duplicate_status=(
            duplicate_status
        ),
        out_of_sequence_status=(
            out_of_sequence_status
        ),
        cursor_after_rejections=(
            cursor_after_rejections
        ),

        first_replay_status=(
            first_replay_status
        ),
        replay_complete_after_first=(
            replay_complete_after_first
        ),
        ready_after_first_replay=(
            ready_after_first_replay
        ),

        second_replay_status=(
            second_replay_status
        ),
        replay_complete_after_second=(
            replay_complete_after_second
        ),
        cursor_after_replay=(
            cursor_after_replay
        ),

        ready_before_reconciliation=(
            ready_before_reconciliation
        ),
        reconciliation_matched=(
            reconciliation_matched
        ),
        ready_before_heartbeat=(
            ready_before_heartbeat
        ),

        heartbeat_healthy=(
            heartbeat_healthy
        ),
        final_readiness=(
            final_readiness
        ),
        resume_status=(
            resume_result.status
        ),
        trading_paused_final=(
            controls.is_paused
        ),
    )


def print_result(
    result: EagleDisconnectRecoveryResult,
) -> None:
    """Print the Eagle disconnect/reconnect test result."""

    if not isinstance(
        result,
        EagleDisconnectRecoveryResult,
    ):
        raise TypeError(
            "'result' must be an "
            "EagleDisconnectRecoveryResult."
        )

    print()
    print(
        "BTS / EAGLE DISCONNECT RECOVERY TEST"
    )
    print(
        "================================================"
    )
    print(
        "Durable cursor before reconnect: "
        f"{result.durable_cursor_before_reconnect}"
    )
    print(
        f"Reconnect URI:                  "
        f"{result.reconnect_uri}"
    )
    print(
        f"Ready before hello:             "
        f"{result.ready_before_hello}"
    )
    print(
        f"Resumed before hello:           "
        f"{result.resumed_before_hello}"
    )
    print(
        f"Hello received:                 "
        f"{result.hello_received}"
    )
    print(
        f"Announced replay count:         "
        f"{result.announced_replay_count}"
    )
    print(
        f"Ready after hello:              "
        f"{result.ready_after_hello}"
    )
    print(
        f"Duplicate replay status:        "
        f"{result.duplicate_status.value}"
    )
    print(
        f"Old-sequence replay status:     "
        f"{result.out_of_sequence_status.value}"
    )
    print(
        f"Cursor after rejections:        "
        f"{result.cursor_after_rejections}"
    )
    print(
        f"First replay status:            "
        f"{result.first_replay_status.value}"
    )
    print(
        f"Replay complete after first:    "
        f"{result.replay_complete_after_first}"
    )
    print(
        f"Second replay status:           "
        f"{result.second_replay_status.value}"
    )
    print(
        f"Replay complete after second:   "
        f"{result.replay_complete_after_second}"
    )
    print(
        f"Cursor after replay:            "
        f"{result.cursor_after_replay}"
    )
    print(
        f"Reconciliation matched:         "
        f"{result.reconciliation_matched}"
    )
    print(
        f"Heartbeat healthy:              "
        f"{result.heartbeat_healthy}"
    )
    print(
        f"Final reconnect readiness:      "
        f"{result.final_readiness}"
    )
    print(
        f"Resume result:                  "
        f"{result.resume_status.value}"
    )
    print(
        f"Trading paused at end:          "
        f"{result.trading_paused_final}"
    )
    print(
        "================================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - Eagle reconnect remained "
            "fail-closed until replay, reconciliation, "
            "and heartbeat recovery completed."
        )

    else:
        print(
            "RESULT: FAIL - Eagle reconnect safety "
            "validation failed."
        )

    print()


def main() -> int:
    """Run the offline Eagle disconnect/recovery test."""

    print()
    print(
        "Starting offline Eagle disconnect/recovery test..."
    )
    print(
        "NO IB connection or order-submission path exists."
    )

    with TemporaryDirectory(
        prefix="bts_eagle_disconnect_"
    ) as temporary_directory:

        database_path = (
            Path(
                temporary_directory
            )
            / "events.db"
        )

        try:
            result = (
                run_eagle_disconnect_recovery_test(
                    database_path=database_path
                )
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