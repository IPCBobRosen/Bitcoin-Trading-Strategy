"""Invalid Eagle lifecycle decision safety harness.

This harness proves that invalid Eagle signal-lifecycle transitions
are rejected by the production TradeCoordinator and never continue
past the TradeDecision boundary.

Scenario for one signal_id:

    1. BUY_TO_OPEN
       NEW -> LONG_OPEN
       expected: APPROVED

    2. BUY_TO_OPEN again
       LONG_OPEN + BUY_TO_OPEN
       expected: REJECTED

    3. SELL_TO_CLOSE
       LONG_OPEN -> CLOSED
       expected: APPROVED

    4. BUY_TO_OPEN again
       CLOSED + BUY_TO_OPEN
       expected: REJECTED

The harness intentionally contains no broker client, execution client,
order factory, risk execution pipeline, or order-submission function.
"""

from dataclasses import dataclass
from pathlib import Path

from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


DEFAULT_EVENT_DATABASE = (
    Path("data")
    / "eagle_invalid_lifecycle_events.db"
)

DEFAULT_LIFECYCLE_DATABASE = (
    Path("data")
    / "eagle_invalid_lifecycle_signals.db"
)

TEST_SIGNAL_ID = "signal-invalid-001"


@dataclass(frozen=True, slots=True)
class InvalidLifecycleDecisionResult:
    """Immutable result of the invalid-lifecycle safety scenario."""

    open_approved: bool
    duplicate_open_rejected: bool
    close_approved: bool
    reopen_rejected: bool

    state_after_open: SignalLifecycleState | None
    state_after_duplicate_open: SignalLifecycleState | None
    state_after_close: SignalLifecycleState | None
    state_after_reopen_attempt: SignalLifecycleState | None

    approved_decision_count: int
    rejected_decision_count: int

    final_event_cursor: int | None

    broker_calls_possible: bool
    order_submission_possible: bool

    @property
    def successful(self) -> bool:
        """Return True only when invalid transitions fail closed."""

        return (
            self.open_approved
            and self.duplicate_open_rejected
            and self.close_approved
            and self.reopen_rejected
            and (
                self.state_after_open
                is SignalLifecycleState.LONG_OPEN
            )
            and (
                self.state_after_duplicate_open
                is SignalLifecycleState.LONG_OPEN
            )
            and (
                self.state_after_close
                is SignalLifecycleState.CLOSED
            )
            and (
                self.state_after_reopen_attempt
                is SignalLifecycleState.CLOSED
            )
            and self.approved_decision_count == 2
            and self.rejected_decision_count == 2
            and self.final_event_cursor == 4
            and self.broker_calls_possible is False
            and self.order_submission_possible is False
        )


def build_event(
    *,
    seq: int,
    event_id: str,
    intent: str,
) -> IncomingLifecycleEvent:
    """Create one validated Eagle lifecycle event."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": seq,
            "event_id": event_id,
            "signal_id": TEST_SIGNAL_ID,
            "ts": "2026-08-17T12:00:00+00:00",
            "env": "staging",
            "payload": {
                "intent": intent,
            },
        }
    )


def build_close_event(
    *,
    seq: int,
    event_id: str,
) -> IncomingLifecycleEvent:
    """Create one validated Eagle close event."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.exit",
            "seq": seq,
            "event_id": event_id,
            "signal_id": TEST_SIGNAL_ID,
            "ts": "2026-08-17T12:00:01+00:00",
            "env": "staging",
            "payload": {
                "intent": "SELL_TO_CLOSE",
            },
        }
    )


def build_coordinator(
    *,
    lifecycle_database_path: str | Path,
) -> TradeCoordinator:
    """Create production TradeCoordinator with decision-only settings."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    controls.resume()

    lifecycle_guard = SignalLifecycleGuard(
        lifecycle_database_path
    )

    return TradeCoordinator(
        controls=controls,
        signal_lifecycle_guard=lifecycle_guard,
    )


def process_accepted_event(
    *,
    event: IncomingLifecycleEvent,
    event_processor: EventProcessor,
    coordinator: TradeCoordinator,
):
    """Run event through durable identity checks and decision layer."""

    event_result = event_processor.process(
        event
    )

    if (
        event_result.status
        is not EventProcessStatus.ACCEPTED
    ):
        raise RuntimeError(
            "Expected test lifecycle event to be accepted "
            "by EventProcessor."
        )

    return coordinator.process_event(
        event
    )


def run_invalid_lifecycle_decision_test(
    *,
    event_database_path: str | Path,
    lifecycle_database_path: str | Path,
) -> InvalidLifecycleDecisionResult:
    """Run the complete invalid lifecycle decision scenario."""

    event_store = EventStore(
        event_database_path
    )

    if event_store.get_last_seq() is not None:
        raise RuntimeError(
            "Invalid lifecycle decision test requires "
            "a fresh event database."
        )

    event_processor = EventProcessor(
        event_store
    )

    coordinator = build_coordinator(
        lifecycle_database_path=(
            lifecycle_database_path
        )
    )

    lifecycle_guard = (
        coordinator.signal_lifecycle_guard
    )

    if (
        lifecycle_guard.get_state(
            TEST_SIGNAL_ID
        )
        is not None
    ):
        raise RuntimeError(
            "Invalid lifecycle decision test requires "
            "a fresh lifecycle database."
        )

    approved_decision_count = 0
    rejected_decision_count = 0

    # ---------------------------------------------------------
    # 1. Valid open.
    # ---------------------------------------------------------

    open_event = build_event(
        seq=1,
        event_id="invalid-test-event-001",
        intent="BUY_TO_OPEN",
    )

    open_decision = process_accepted_event(
        event=open_event,
        event_processor=event_processor,
        coordinator=coordinator,
    )

    open_approved = (
        open_decision.approved
    )

    if open_approved:
        approved_decision_count += 1
    else:
        rejected_decision_count += 1

    state_after_open = (
        lifecycle_guard.get_state(
            TEST_SIGNAL_ID
        )
    )

    print()
    print(
        "STEP 1 - VALID OPEN"
    )
    print(
        f"Decision approved: {open_decision.approved}"
    )
    print(
        f"Decision reason:   {open_decision.reason}"
    )
    print(
        f"Lifecycle state:   {state_after_open}"
    )

    # ---------------------------------------------------------
    # 2. Invalid duplicate open on same signal.
    # ---------------------------------------------------------

    duplicate_open_event = build_event(
        seq=2,
        event_id="invalid-test-event-002",
        intent="BUY_TO_OPEN",
    )

    duplicate_open_decision = (
        process_accepted_event(
            event=duplicate_open_event,
            event_processor=event_processor,
            coordinator=coordinator,
        )
    )

    duplicate_open_rejected = (
        not duplicate_open_decision.approved
    )

    if duplicate_open_decision.approved:
        approved_decision_count += 1
    else:
        rejected_decision_count += 1

    state_after_duplicate_open = (
        lifecycle_guard.get_state(
            TEST_SIGNAL_ID
        )
    )

    print()
    print(
        "STEP 2 - INVALID SECOND OPEN"
    )
    print(
        "Decision approved: "
        f"{duplicate_open_decision.approved}"
    )
    print(
        "Decision reason:   "
        f"{duplicate_open_decision.reason}"
    )
    print(
        "Lifecycle state:   "
        f"{state_after_duplicate_open}"
    )

    # ---------------------------------------------------------
    # 3. Valid close after rejected duplicate open.
    # ---------------------------------------------------------

    close_event = build_close_event(
        seq=3,
        event_id="invalid-test-event-003",
    )

    close_decision = process_accepted_event(
        event=close_event,
        event_processor=event_processor,
        coordinator=coordinator,
    )

    close_approved = (
        close_decision.approved
    )

    if close_decision.approved:
        approved_decision_count += 1
    else:
        rejected_decision_count += 1

    state_after_close = (
        lifecycle_guard.get_state(
            TEST_SIGNAL_ID
        )
    )

    print()
    print(
        "STEP 3 - VALID CLOSE"
    )
    print(
        f"Decision approved: {close_decision.approved}"
    )
    print(
        f"Decision reason:   {close_decision.reason}"
    )
    print(
        f"Lifecycle state:   {state_after_close}"
    )

    # ---------------------------------------------------------
    # 4. Invalid attempt to reuse CLOSED signal ID.
    # ---------------------------------------------------------

    reopen_event = build_event(
        seq=4,
        event_id="invalid-test-event-004",
        intent="BUY_TO_OPEN",
    )

    reopen_decision = process_accepted_event(
        event=reopen_event,
        event_processor=event_processor,
        coordinator=coordinator,
    )

    reopen_rejected = (
        not reopen_decision.approved
    )

    if reopen_decision.approved:
        approved_decision_count += 1
    else:
        rejected_decision_count += 1

    state_after_reopen_attempt = (
        lifecycle_guard.get_state(
            TEST_SIGNAL_ID
        )
    )

    print()
    print(
        "STEP 4 - INVALID REOPEN OF CLOSED SIGNAL"
    )
    print(
        f"Decision approved: {reopen_decision.approved}"
    )
    print(
        f"Decision reason:   {reopen_decision.reason}"
    )
    print(
        "Lifecycle state:   "
        f"{state_after_reopen_attempt}"
    )

    return InvalidLifecycleDecisionResult(
        open_approved=(
            open_approved
        ),

        duplicate_open_rejected=(
            duplicate_open_rejected
        ),

        close_approved=(
            close_approved
        ),

        reopen_rejected=(
            reopen_rejected
        ),

        state_after_open=(
            state_after_open
        ),

        state_after_duplicate_open=(
            state_after_duplicate_open
        ),

        state_after_close=(
            state_after_close
        ),

        state_after_reopen_attempt=(
            state_after_reopen_attempt
        ),

        approved_decision_count=(
            approved_decision_count
        ),

        rejected_decision_count=(
            rejected_decision_count
        ),

        final_event_cursor=(
            event_store.get_last_seq()
        ),

        broker_calls_possible=False,
        order_submission_possible=False,
    )


def print_result(
    result: InvalidLifecycleDecisionResult,
) -> None:
    """Print the invalid lifecycle test summary."""

    if not isinstance(
        result,
        InvalidLifecycleDecisionResult,
    ):
        raise TypeError(
            "'result' must be an "
            "InvalidLifecycleDecisionResult."
        )

    print()
    print(
        "BTS / EAGLE INVALID LIFECYCLE DECISION TEST"
    )
    print("=" * 60)

    print(
        f"Valid open approved:          "
        f"{result.open_approved}"
    )

    print(
        f"Second open rejected:        "
        f"{result.duplicate_open_rejected}"
    )

    print(
        f"Valid close approved:         "
        f"{result.close_approved}"
    )

    print(
        f"Closed-signal reopen rejected:"
        f" {result.reopen_rejected}"
    )

    print(
        f"State after open:             "
        f"{result.state_after_open}"
    )

    print(
        f"State after invalid open:     "
        f"{result.state_after_duplicate_open}"
    )

    print(
        f"State after close:            "
        f"{result.state_after_close}"
    )

    print(
        f"State after reopen attempt:   "
        f"{result.state_after_reopen_attempt}"
    )

    print(
        f"Approved decisions:           "
        f"{result.approved_decision_count}"
    )

    print(
        f"Rejected decisions:           "
        f"{result.rejected_decision_count}"
    )

    print(
        f"Final durable event cursor:   "
        f"{result.final_event_cursor}"
    )

    print("=" * 60)

    print(
        "NO BROKER CALLS WERE POSSIBLE."
    )

    print(
        "NO ORDERS WERE POSSIBLE."
    )

    if result.successful:
        print()
        print(
            "RESULT: PASS - invalid signal lifecycle "
            "transitions were rejected without corrupting "
            "durable signal state."
        )

    else:
        print()
        print(
            "RESULT: FAIL - invalid lifecycle safety "
            "validation failed."
        )


def main() -> int:
    """Run the invalid lifecycle decision test."""

    print()
    print(
        "Starting invalid Eagle lifecycle decision test..."
    )

    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )

    try:
        result = (
            run_invalid_lifecycle_decision_test(
                event_database_path=(
                    DEFAULT_EVENT_DATABASE
                ),
                lifecycle_database_path=(
                    DEFAULT_LIFECYCLE_DATABASE
                ),
            )
        )

    except Exception as error:
        print()
        print(
            "RESULT: FAIL"
        )

        print(
            f"{type(error).__name__}: "
            f"{error}"
        )

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