"""Tests for the invalid Eagle lifecycle decision safety harness."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import EventProcessor
from app.event_store import EventStore
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)
from app.trade_coordinator import TradeCoordinator

from scripts.test_eagle_invalid_lifecycle_decisions import (
    DEFAULT_EVENT_DATABASE,
    DEFAULT_LIFECYCLE_DATABASE,
    TEST_SIGNAL_ID,
    InvalidLifecycleDecisionResult,
    build_close_event,
    build_coordinator,
    build_event,
    process_accepted_event,
    run_invalid_lifecycle_decision_test,
)


def script_source() -> str:
    """Return complete invalid-lifecycle harness source."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_invalid_lifecycle_decisions.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def create_success_result() -> InvalidLifecycleDecisionResult:
    """Create one representative successful safety result."""

    return InvalidLifecycleDecisionResult(
        open_approved=True,
        duplicate_open_rejected=True,
        close_approved=True,
        reopen_rejected=True,

        state_after_open=(
            SignalLifecycleState.LONG_OPEN
        ),

        state_after_duplicate_open=(
            SignalLifecycleState.LONG_OPEN
        ),

        state_after_close=(
            SignalLifecycleState.CLOSED
        ),

        state_after_reopen_attempt=(
            SignalLifecycleState.CLOSED
        ),

        approved_decision_count=2,
        rejected_decision_count=2,

        final_event_cursor=4,

        broker_calls_possible=False,
        order_submission_possible=False,
    )


def test_default_event_database_name() -> None:
    """Harness should use isolated event-state database."""

    assert (
        DEFAULT_EVENT_DATABASE.name
        == "eagle_invalid_lifecycle_events.db"
    )


def test_default_lifecycle_database_name() -> None:
    """Harness should use isolated lifecycle database."""

    assert (
        DEFAULT_LIFECYCLE_DATABASE.name
        == "eagle_invalid_lifecycle_signals.db"
    )


def test_test_signal_id() -> None:
    """Harness should use a deterministic signal ID."""

    assert TEST_SIGNAL_ID == "signal-invalid-001"


def test_result_is_immutable() -> None:
    """Safety result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_event_cursor = 99  # type: ignore[misc]


def test_success_result_is_successful() -> None:
    """Representative fail-closed scenario should pass."""

    result = create_success_result()

    assert result.successful is True


def test_success_requires_valid_open() -> None:
    """Scenario cannot pass if initial open is rejected."""

    result = create_success_result()

    modified = InvalidLifecycleDecisionResult(
        open_approved=False,
        duplicate_open_rejected=(
            result.duplicate_open_rejected
        ),
        close_approved=result.close_approved,
        reopen_rejected=result.reopen_rejected,

        state_after_open=result.state_after_open,

        state_after_duplicate_open=(
            result.state_after_duplicate_open
        ),

        state_after_close=(
            result.state_after_close
        ),

        state_after_reopen_attempt=(
            result.state_after_reopen_attempt
        ),

        approved_decision_count=(
            result.approved_decision_count
        ),

        rejected_decision_count=(
            result.rejected_decision_count
        ),

        final_event_cursor=(
            result.final_event_cursor
        ),

        broker_calls_possible=False,
        order_submission_possible=False,
    )

    assert modified.successful is False


def test_success_requires_duplicate_open_rejection() -> None:
    """Second BUY_TO_OPEN must be rejected."""

    result = create_success_result()

    modified = InvalidLifecycleDecisionResult(
        open_approved=result.open_approved,
        duplicate_open_rejected=False,
        close_approved=result.close_approved,
        reopen_rejected=result.reopen_rejected,

        state_after_open=result.state_after_open,

        state_after_duplicate_open=(
            result.state_after_duplicate_open
        ),

        state_after_close=(
            result.state_after_close
        ),

        state_after_reopen_attempt=(
            result.state_after_reopen_attempt
        ),

        approved_decision_count=(
            result.approved_decision_count
        ),

        rejected_decision_count=(
            result.rejected_decision_count
        ),

        final_event_cursor=(
            result.final_event_cursor
        ),

        broker_calls_possible=False,
        order_submission_possible=False,
    )

    assert modified.successful is False


def test_success_requires_valid_close() -> None:
    """Valid SELL_TO_CLOSE must still work after rejection."""

    result = create_success_result()

    modified = InvalidLifecycleDecisionResult(
        open_approved=result.open_approved,
        duplicate_open_rejected=(
            result.duplicate_open_rejected
        ),
        close_approved=False,
        reopen_rejected=result.reopen_rejected,

        state_after_open=result.state_after_open,

        state_after_duplicate_open=(
            result.state_after_duplicate_open
        ),

        state_after_close=(
            result.state_after_close
        ),

        state_after_reopen_attempt=(
            result.state_after_reopen_attempt
        ),

        approved_decision_count=(
            result.approved_decision_count
        ),

        rejected_decision_count=(
            result.rejected_decision_count
        ),

        final_event_cursor=(
            result.final_event_cursor
        ),

        broker_calls_possible=False,
        order_submission_possible=False,
    )

    assert modified.successful is False


def test_success_requires_closed_signal_reopen_rejection() -> None:
    """Closed signal ID must not be reusable."""

    result = create_success_result()

    modified = InvalidLifecycleDecisionResult(
        open_approved=result.open_approved,
        duplicate_open_rejected=(
            result.duplicate_open_rejected
        ),
        close_approved=result.close_approved,
        reopen_rejected=False,

        state_after_open=result.state_after_open,

        state_after_duplicate_open=(
            result.state_after_duplicate_open
        ),

        state_after_close=(
            result.state_after_close
        ),

        state_after_reopen_attempt=(
            result.state_after_reopen_attempt
        ),

        approved_decision_count=(
            result.approved_decision_count
        ),

        rejected_decision_count=(
            result.rejected_decision_count
        ),

        final_event_cursor=(
            result.final_event_cursor
        ),

        broker_calls_possible=False,
        order_submission_possible=False,
    )

    assert modified.successful is False


def test_success_requires_long_state_after_open() -> None:
    """Initial BUY_TO_OPEN must persist LONG_OPEN."""

    result = create_success_result()

    assert (
        result.state_after_open
        is SignalLifecycleState.LONG_OPEN
    )


def test_invalid_second_open_does_not_change_state() -> None:
    """Rejected duplicate open must preserve LONG_OPEN."""

    result = create_success_result()

    assert (
        result.state_after_duplicate_open
        is SignalLifecycleState.LONG_OPEN
    )


def test_valid_close_persists_closed() -> None:
    """Valid close must persist CLOSED."""

    result = create_success_result()

    assert (
        result.state_after_close
        is SignalLifecycleState.CLOSED
    )


def test_invalid_reopen_does_not_change_closed_state() -> None:
    """Rejected reopen must preserve CLOSED."""

    result = create_success_result()

    assert (
        result.state_after_reopen_attempt
        is SignalLifecycleState.CLOSED
    )


def test_expected_decision_counts() -> None:
    """Scenario should contain two approvals and two rejections."""

    result = create_success_result()

    assert result.approved_decision_count == 2
    assert result.rejected_decision_count == 2


def test_expected_final_cursor() -> None:
    """All four Eagle events should be durably accepted."""

    result = create_success_result()

    assert result.final_event_cursor == 4


def test_success_requires_no_broker_calls() -> None:
    """Broker capability must fail the safety result."""

    result = create_success_result()

    modified = InvalidLifecycleDecisionResult(
        open_approved=result.open_approved,
        duplicate_open_rejected=(
            result.duplicate_open_rejected
        ),
        close_approved=result.close_approved,
        reopen_rejected=result.reopen_rejected,

        state_after_open=result.state_after_open,

        state_after_duplicate_open=(
            result.state_after_duplicate_open
        ),

        state_after_close=(
            result.state_after_close
        ),

        state_after_reopen_attempt=(
            result.state_after_reopen_attempt
        ),

        approved_decision_count=(
            result.approved_decision_count
        ),

        rejected_decision_count=(
            result.rejected_decision_count
        ),

        final_event_cursor=(
            result.final_event_cursor
        ),

        broker_calls_possible=True,
        order_submission_possible=False,
    )

    assert modified.successful is False


def test_success_requires_no_order_submission() -> None:
    """Order capability must fail the safety result."""

    result = create_success_result()

    modified = InvalidLifecycleDecisionResult(
        open_approved=result.open_approved,
        duplicate_open_rejected=(
            result.duplicate_open_rejected
        ),
        close_approved=result.close_approved,
        reopen_rejected=result.reopen_rejected,

        state_after_open=result.state_after_open,

        state_after_duplicate_open=(
            result.state_after_duplicate_open
        ),

        state_after_close=(
            result.state_after_close
        ),

        state_after_reopen_attempt=(
            result.state_after_reopen_attempt
        ),

        approved_decision_count=(
            result.approved_decision_count
        ),

        rejected_decision_count=(
            result.rejected_decision_count
        ),

        final_event_cursor=(
            result.final_event_cursor
        ),

        broker_calls_possible=False,
        order_submission_possible=True,
    )

    assert modified.successful is False


def test_build_event_creates_valid_entry() -> None:
    """Entry helper should create validated Eagle event."""

    event = build_event(
        seq=1,
        event_id="event-001",
        intent="BUY_TO_OPEN",
    )

    assert isinstance(
        event,
        IncomingLifecycleEvent,
    )

    assert event.message_type == "fund.entry"
    assert event.seq == 1
    assert event.event_id == "event-001"
    assert event.signal_id == TEST_SIGNAL_ID

    assert (
        event.payload["intent"]
        == "BUY_TO_OPEN"
    )


def test_build_close_event_creates_valid_exit() -> None:
    """Close helper should create validated fund.exit."""

    event = build_close_event(
        seq=3,
        event_id="event-003",
    )

    assert isinstance(
        event,
        IncomingLifecycleEvent,
    )

    assert event.message_type == "fund.exit"
    assert event.seq == 3
    assert event.signal_id == TEST_SIGNAL_ID

    assert (
        event.payload["intent"]
        == "SELL_TO_CLOSE"
    )


def test_build_coordinator_uses_production_components(
    tmp_path,
) -> None:
    """Harness should use production decision layer."""

    coordinator = build_coordinator(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        )
    )

    assert isinstance(
        coordinator,
        TradeCoordinator,
    )

    assert isinstance(
        coordinator.signal_lifecycle_guard,
        SignalLifecycleGuard,
    )

    assert coordinator.controls.is_paused is False


def test_process_accepted_event_reaches_coordinator(
    tmp_path,
) -> None:
    """Accepted Eagle event should reach decision layer."""

    event_store = EventStore(
        tmp_path
        / "events.db"
    )

    processor = EventProcessor(
        event_store
    )

    coordinator = build_coordinator(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        )
    )

    event = build_event(
        seq=1,
        event_id="event-001",
        intent="BUY_TO_OPEN",
    )

    decision = process_accepted_event(
        event=event,
        event_processor=processor,
        coordinator=coordinator,
    )

    assert decision.approved is True


def test_complete_scenario_passes(
    tmp_path,
) -> None:
    """Full invalid-lifecycle scenario should pass offline."""

    result = (
        run_invalid_lifecycle_decision_test(
            event_database_path=(
                tmp_path
                / "events.db"
            ),
            lifecycle_database_path=(
                tmp_path
                / "signals.db"
            ),
        )
    )

    assert result.successful is True
    assert result.final_event_cursor == 4

    assert (
        result.state_after_duplicate_open
        is SignalLifecycleState.LONG_OPEN
    )

    assert (
        result.state_after_reopen_attempt
        is SignalLifecycleState.CLOSED
    )


def test_script_uses_event_processor() -> None:
    """Events must pass durable identity/sequence checks."""

    source = script_source()

    assert "EventProcessor(" in source
    assert "event_processor.process(" in source


def test_script_uses_trade_coordinator() -> None:
    """Events must reach production decision layer."""

    source = script_source()

    assert "TradeCoordinator(" in source
    assert "coordinator.process_event(" in source


def test_script_uses_signal_lifecycle_guard() -> None:
    """Harness must use durable lifecycle state."""

    source = script_source()

    assert "SignalLifecycleGuard(" in source
    assert "lifecycle_guard.get_state(" in source


def test_script_contains_invalid_second_open() -> None:
    """Harness must deliberately replay BUY_TO_OPEN."""

    source = script_source()

    assert "STEP 2 - INVALID SECOND OPEN" in source


def test_script_contains_closed_signal_reopen() -> None:
    """Harness must deliberately try reusing CLOSED signal."""

    source = script_source()

    assert (
        "STEP 4 - INVALID REOPEN OF CLOSED SIGNAL"
        in source
    )


def test_script_contains_no_ib_execution_path() -> None:
    """Harness must contain no IB execution code."""

    source = script_source()

    forbidden_tokens = (
        "ibapi",
        "IBExecutionClient",
        "IBOrderFactory",
        "IBBrokerClient",
        "placeOrder",
        "place_order_function",
    )

    for token in forbidden_tokens:
        assert token not in source


def test_script_contains_no_broker_client() -> None:
    """Harness must contain no broker connection."""

    source = script_source()

    forbidden_tokens = (
        "FakeBrokerClient",
        "BrokerPositionProvider",
    )

    for token in forbidden_tokens:
        assert token not in source


def test_script_contains_no_risk_manager() -> None:
    """Harness must stop before risk/execution pipeline."""

    source = script_source()

    assert "RiskManager" not in source

    assert (
        "from app.risk_manager import"
        not in source
    )


def test_script_contains_no_order_factory() -> None:
    """Harness must not construct broker orders."""

    source = script_source()

    assert "OrderFactory" not in source
    assert "order_factory" not in source


def test_script_contains_no_execution_client() -> None:
    """Harness must not contain execution client."""

    source = script_source()

    assert "ExecutionClient" not in source
    assert "execution_client" not in source