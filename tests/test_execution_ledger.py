"""Tests for the durable BTS execution ledger."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionRecord,
    ExecutionStatus,
)


def create_trade_request(
    *,
    event_id: str = "event-001",
    signal_id: str = "signal-001",
    intent_value: str = "BUY_TO_OPEN",
    quantity: int = 1,
) -> TradeRequest:
    """Create a deterministic TradeRequest."""

    return TradeRequest(
        event_id=event_id,
        signal_id=signal_id,
        timestamp=datetime(
            2026,
            8,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent(
            intent_value
        ),
        symbol="MBT",
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def create_ledger(
    tmp_path,
) -> ExecutionLedger:
    """Create a temporary durable ledger."""

    return ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )


def test_new_ledger_is_empty(
    tmp_path,
) -> None:
    """New ledger should contain no execution records."""

    ledger = create_ledger(
        tmp_path
    )

    assert ledger.all_records() == ()


def test_reserve_creates_reserved_record(
    tmp_path,
) -> None:
    """First reservation should create durable RESERVED state."""

    ledger = create_ledger(
        tmp_path
    )

    result = ledger.reserve(
        create_trade_request()
    )

    assert isinstance(
        result,
        ExecutionRecord,
    )

    assert (
        result.status
        is ExecutionStatus.RESERVED
    )

    assert result.event_id == "event-001"


def test_reservation_is_durable_across_instances(
    tmp_path,
) -> None:
    """A new ledger instance should see previous reservation."""

    database_path = (
        tmp_path
        / "execution_ledger.db"
    )

    first = ExecutionLedger(
        database_path
    )

    first.reserve(
        create_trade_request()
    )

    second = ExecutionLedger(
        database_path
    )

    result = second.get(
        "event-001"
    )

    assert result is not None
    assert (
        result.status
        is ExecutionStatus.RESERVED
    )


def test_duplicate_event_cannot_be_reserved(
    tmp_path,
) -> None:
    """event_id must be durable idempotency key."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    with pytest.raises(
        ValueError,
        match="already exists",
    ):
        ledger.reserve(
            create_trade_request()
        )


def test_contains_returns_true_for_known_event(
    tmp_path,
) -> None:
    """Known event should be discoverable."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    assert (
        ledger.contains(
            "event-001"
        )
        is True
    )


def test_contains_returns_false_for_unknown_event(
    tmp_path,
) -> None:
    """Unknown event should not be present."""

    ledger = create_ledger(
        tmp_path
    )

    assert (
        ledger.contains(
            "missing-event"
        )
        is False
    )


def test_mark_submitted_records_broker_order_id(
    tmp_path,
) -> None:
    """Submission should persist broker order ID."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    result = ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    assert (
        result.status
        is ExecutionStatus.SUBMITTED
    )

    assert result.broker_order_id == 123


def test_submitted_can_be_acknowledged(
    tmp_path,
) -> None:
    """Submitted order may become acknowledged."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    result = (
        ledger.mark_acknowledged(
            "event-001"
        )
    )

    assert (
        result.status
        is ExecutionStatus.ACKNOWLEDGED
    )


def test_acknowledged_can_be_partially_filled(
    tmp_path,
) -> None:
    """Acknowledged order may receive a partial fill."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    ledger.mark_acknowledged(
        "event-001"
    )

    result = (
        ledger.mark_partially_filled(
            "event-001"
        )
    )

    assert (
        result.status
        is ExecutionStatus.PARTIALLY_FILLED
    )


def test_partial_fill_can_become_filled(
    tmp_path,
) -> None:
    """Partially filled order may complete."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    ledger.mark_partially_filled(
        "event-001"
    )

    result = ledger.mark_filled(
        "event-001"
    )

    assert (
        result.status
        is ExecutionStatus.FILLED
    )

    assert result.terminal is True


def test_submitted_can_fill_without_ack_callback(
    tmp_path,
) -> None:
    """Broker callback ordering may skip explicit acknowledgement."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    result = ledger.mark_filled(
        "event-001"
    )

    assert (
        result.status
        is ExecutionStatus.FILLED
    )


def test_reserved_can_be_rejected(
    tmp_path,
) -> None:
    """Locally reserved execution may fail before submission."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    result = ledger.mark_rejected(
        "event-001",
        reason="Local execution validation failed.",
    )

    assert (
        result.status
        is ExecutionStatus.REJECTED
    )

    assert (
        result.reason
        == "Local execution validation failed."
    )


def test_submitted_can_be_cancelled(
    tmp_path,
) -> None:
    """Submitted order may be cancelled."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    result = ledger.mark_cancelled(
        "event-001",
        reason="Operator cancelled.",
    )

    assert (
        result.status
        is ExecutionStatus.CANCELLED
    )

    assert result.terminal is True


@pytest.mark.parametrize(
    "terminal_status",
    [
        ExecutionStatus.FILLED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.REJECTED,
    ],
)
def test_terminal_records_are_terminal(
    tmp_path,
    terminal_status,
) -> None:
    """Final states must report terminal status."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    if (
        terminal_status
        is ExecutionStatus.REJECTED
    ):
        result = ledger.mark_rejected(
            "event-001",
            reason="Rejected.",
        )

    else:
        ledger.mark_submitted(
            "event-001",
            broker_order_id=123,
        )

        if (
            terminal_status
            is ExecutionStatus.FILLED
        ):
            result = ledger.mark_filled(
                "event-001"
            )

        else:
            result = ledger.mark_cancelled(
                "event-001"
            )

    assert result.terminal is True


def test_terminal_state_cannot_transition_again(
    tmp_path,
) -> None:
    """Filled execution must remain final."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    ledger.mark_filled(
        "event-001"
    )

    with pytest.raises(
        ValueError,
        match="Invalid execution transition",
    ):
        ledger.mark_cancelled(
            "event-001"
        )


def test_invalid_transition_is_rejected(
    tmp_path,
) -> None:
    """RESERVED cannot jump directly to FILLED."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    with pytest.raises(
        ValueError,
        match="Invalid execution transition",
    ):
        ledger.mark_filled(
            "event-001"
        )


def test_unknown_event_transition_is_rejected(
    tmp_path,
) -> None:
    """Transition requires a known durable event."""

    ledger = create_ledger(
        tmp_path
    )

    with pytest.raises(
        KeyError,
        match="does not exist",
    ):
        ledger.mark_submitted(
            "missing-event",
            broker_order_id=123,
        )


def test_history_begins_with_reservation(
    tmp_path,
) -> None:
    """Reservation must create first audit-history row."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    history = ledger.history(
        "event-001"
    )

    assert len(history) == 1
    assert history[0].from_status is None

    assert (
        history[0].to_status
        is ExecutionStatus.RESERVED
    )


def test_history_records_complete_lifecycle(
    tmp_path,
) -> None:
    """Every lifecycle transition should be durable."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=123,
    )

    ledger.mark_acknowledged(
        "event-001"
    )

    ledger.mark_partially_filled(
        "event-001"
    )

    ledger.mark_filled(
        "event-001"
    )

    history = ledger.history(
        "event-001"
    )

    assert tuple(
        transition.to_status
        for transition in history
    ) == (
        ExecutionStatus.RESERVED,
        ExecutionStatus.SUBMITTED,
        ExecutionStatus.ACKNOWLEDGED,
        ExecutionStatus.PARTIALLY_FILLED,
        ExecutionStatus.FILLED,
    )


def test_broker_order_id_persists_through_transitions(
    tmp_path,
) -> None:
    """Broker order ID should remain attached to lifecycle."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=456,
    )

    result = ledger.mark_acknowledged(
        "event-001"
    )

    assert result.broker_order_id == 456


def test_multiple_events_are_independent(
    tmp_path,
) -> None:
    """Ledger should independently track multiple events."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request(
            event_id="event-001",
            signal_id="signal-001",
        )
    )

    ledger.reserve(
        create_trade_request(
            event_id="event-002",
            signal_id="signal-002",
        )
    )

    assert len(
        ledger.all_records()
    ) == 2


def test_same_signal_can_have_entry_and_exit_events(
    tmp_path,
) -> None:
    """Distinct event IDs may share one Eagle signal ID."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request(
            event_id="entry-001",
            signal_id="signal-001",
            intent_value="BUY_TO_OPEN",
        )
    )

    ledger.reserve(
        create_trade_request(
            event_id="exit-001",
            signal_id="signal-001",
            intent_value="SELL_TO_CLOSE",
        )
    )

    assert len(
        ledger.all_records()
    ) == 2


def test_rejected_reason_is_stripped(
    tmp_path,
) -> None:
    """Audit reasons should be normalized."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    result = ledger.mark_rejected(
        "event-001",
        reason="  Broker rejected order.  ",
    )

    assert (
        result.reason
        == "Broker rejected order."
    )


def test_empty_rejection_reason_is_rejected(
    tmp_path,
) -> None:
    """Rejected state requires an explanatory reason."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    with pytest.raises(
        ValueError,
        match="'reason' must be a non-empty string",
    ):
        ledger.mark_rejected(
            "event-001",
            reason="   ",
        )


def test_invalid_broker_order_id_is_rejected(
    tmp_path,
) -> None:
    """Broker order IDs must be non-negative integers."""

    ledger = create_ledger(
        tmp_path
    )

    ledger.reserve(
        create_trade_request()
    )

    with pytest.raises(
        ValueError,
        match="'broker_order_id'",
    ):
        ledger.mark_submitted(
            "event-001",
            broker_order_id=-1,
        )


def test_invalid_trade_request_is_rejected(
    tmp_path,
) -> None:
    """Ledger reserve requires TradeRequest."""

    ledger = create_ledger(
        tmp_path
    )

    with pytest.raises(
        TypeError,
        match="'trade_request' must be a TradeRequest",
    ):
        ledger.reserve(
            object()  # type: ignore[arg-type]
        )


def test_invalid_database_path_is_rejected() -> None:
    """Ledger requires a usable database path."""

    with pytest.raises(
        ValueError,
        match="'database_path'",
    ):
        ExecutionLedger(
            ""
        )


def test_record_timestamps_are_timezone_aware(
    tmp_path,
) -> None:
    """Persistent timestamps must retain timezone information."""

    ledger = create_ledger(
        tmp_path
    )

    result = ledger.reserve(
        create_trade_request()
    )

    assert result.created_at.tzinfo is not None
    assert result.updated_at.tzinfo is not None