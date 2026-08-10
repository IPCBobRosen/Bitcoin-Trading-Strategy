"""Tests for the BTS Interactive Brokers order-status transport."""

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
    ExecutionStatus,
)
from app.ib_order_status_transport import (
    IBOrderStatusTransport,
    IBStatusOutcome,
)


def create_trade_request(
    *,
    event_id: str = "event-001",
) -> TradeRequest:
    """Create a deterministic TradeRequest."""

    return TradeRequest(
        event_id=event_id,
        signal_id="signal-001",
        timestamp=datetime(
            2026,
            8,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol="MBT",
        quantity=5,
        stop_loss_points=Decimal("500"),
    )


def create_submitted_ledger(
    tmp_path,
    *,
    broker_order_id: int = 100,
) -> ExecutionLedger:
    """Create a ledger containing one submitted order."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=broker_order_id,
    )

    return ledger


def create_transport(
    tmp_path,
    *,
    broker_order_id: int = 100,
):
    """Create a submitted ledger and callback transport."""

    ledger = create_submitted_ledger(
        tmp_path,
        broker_order_id=broker_order_id,
    )

    transport = IBOrderStatusTransport(
        ledger
    )

    return transport, ledger


def test_transport_retains_ledger(
    tmp_path,
) -> None:
    """Transport should retain durable ledger dependency."""

    transport, ledger = create_transport(
        tmp_path
    )

    assert (
        transport.execution_ledger
        is ledger
    )


@pytest.mark.parametrize(
    "ib_status",
    [
        "PendingSubmit",
        "PreSubmitted",
        "Submitted",
    ],
)
def test_active_ib_status_acknowledges_submission(
    tmp_path,
    ib_status: str,
) -> None:
    """Active IB states should acknowledge submitted order."""

    transport, ledger = create_transport(
        tmp_path
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status=ib_status,
        filled=0,
        remaining=5,
    )

    assert (
        result.outcome
        is IBStatusOutcome.UPDATED
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.ACKNOWLEDGED
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.ACKNOWLEDGED
    )


def test_partial_fill_advances_from_submitted(
    tmp_path,
) -> None:
    """Partial execution may arrive without acknowledgement first."""

    transport, _ = create_transport(
        tmp_path
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=2,
        remaining=3,
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.PARTIALLY_FILLED
    )


def test_partial_fill_advances_from_acknowledged(
    tmp_path,
) -> None:
    """Acknowledged order may later partially fill."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=0,
        remaining=5,
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=2,
        remaining=3,
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.PARTIALLY_FILLED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.PARTIALLY_FILLED
    )


def test_filled_can_advance_directly_from_submitted(
    tmp_path,
) -> None:
    """Fast fill may skip intermediate order-status states."""

    transport, _ = create_transport(
        tmp_path
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Filled",
        filled=5,
        remaining=0,
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.FILLED
    )


def test_filled_advances_from_partial_fill(
    tmp_path,
) -> None:
    """Partial fill should later be able to complete."""

    transport, _ = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=2,
        remaining=3,
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Filled",
        filled=5,
        remaining=0,
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.FILLED
    )

    assert result.execution_record.terminal is True


def test_cancelled_marks_order_cancelled(
    tmp_path,
) -> None:
    """Confirmed IB cancellation should be durable."""

    transport, _ = create_transport(
        tmp_path
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Cancelled",
        filled=0,
        remaining=5,
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.CANCELLED
    )


def test_api_cancelled_marks_order_cancelled(
    tmp_path,
) -> None:
    """IB ApiCancelled should be treated as confirmed cancellation."""

    transport, _ = create_transport(
        tmp_path
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="ApiCancelled",
        filled=0,
        remaining=5,
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.CANCELLED
    )


@pytest.mark.parametrize(
    "ib_status",
    [
        "PendingCancel",
        "PreCancelled",
        "Inactive",
    ],
)
def test_nonfinal_informational_status_is_ignored(
    tmp_path,
    ib_status: str,
) -> None:
    """Non-final status should not manufacture terminal BTS state."""

    transport, ledger = create_transport(
        tmp_path
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status=ib_status,
        filled=0,
        remaining=5,
    )

    assert (
        result.outcome
        is IBStatusOutcome.IGNORED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.SUBMITTED
    )


def test_duplicate_submitted_callback_is_no_change(
    tmp_path,
) -> None:
    """Repeated IB status should be safely idempotent."""

    transport, _ = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=0,
        remaining=5,
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=0,
        remaining=5,
    )

    assert (
        result.outcome
        is IBStatusOutcome.NO_CHANGE
    )


def test_duplicate_partial_fill_callback_is_no_change(
    tmp_path,
) -> None:
    """Duplicate partial-fill status must not add bad transitions."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=2,
        remaining=3,
    )

    history_before = ledger.history(
        "event-001"
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=2,
        remaining=3,
    )

    history_after = ledger.history(
        "event-001"
    )

    assert (
        result.outcome
        is IBStatusOutcome.NO_CHANGE
    )

    assert history_after == history_before


def test_duplicate_filled_callback_is_no_change(
    tmp_path,
) -> None:
    """Repeated Filled callback should not fail terminal ledger."""

    transport, _ = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Filled",
        filled=5,
        remaining=0,
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Filled",
        filled=5,
        remaining=0,
    )

    assert (
        result.outcome
        is IBStatusOutcome.NO_CHANGE
    )


def test_stale_submitted_after_partial_fill_is_ignored(
    tmp_path,
) -> None:
    """Late acknowledgement must not regress partial-fill state."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=2,
        remaining=3,
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=0,
        remaining=5,
    )

    assert (
        result.outcome
        is IBStatusOutcome.IGNORED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.PARTIALLY_FILLED
    )


def test_stale_submitted_after_fill_is_ignored(
    tmp_path,
) -> None:
    """Late active status must not regress a filled execution."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Filled",
        filled=5,
        remaining=0,
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="Submitted",
        filled=0,
        remaining=5,
    )

    assert (
        result.outcome
        is IBStatusOutcome.IGNORED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.FILLED
    )


def test_confirmed_rejection_marks_rejected(
    tmp_path,
) -> None:
    """Explicit broker rejection should be durable."""

    transport, ledger = create_transport(
        tmp_path
    )

    result = transport.handle_rejection(
        broker_order_id=100,
        reason="IB rejected the order.",
    )

    assert (
        result.outcome
        is IBStatusOutcome.UPDATED
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.REJECTED
    )

    assert (
        ledger.get("event-001").reason
        == "IB rejected the order."
    )


def test_duplicate_rejection_is_no_change(
    tmp_path,
) -> None:
    """Repeated confirmed rejection must be idempotent."""

    transport, _ = create_transport(
        tmp_path
    )

    transport.handle_rejection(
        broker_order_id=100,
        reason="Rejected.",
    )

    result = transport.handle_rejection(
        broker_order_id=100,
        reason="Rejected again.",
    )

    assert (
        result.outcome
        is IBStatusOutcome.NO_CHANGE
    )


def test_rejection_after_fill_is_ignored(
    tmp_path,
) -> None:
    """Late rejection must not overwrite confirmed fill."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_order_status(
        broker_order_id=100,
        status="Filled",
        filled=5,
        remaining=0,
    )

    result = transport.handle_rejection(
        broker_order_id=100,
        reason="Late error.",
    )

    assert (
        result.outcome
        is IBStatusOutcome.IGNORED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.FILLED
    )


def test_unknown_broker_order_id_is_rejected(
    tmp_path,
) -> None:
    """Callbacks must correspond to a durable BTS execution."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        KeyError,
        match="broker order ID 999",
    ):
        transport.handle_order_status(
            broker_order_id=999,
            status="Submitted",
            filled=0,
            remaining=5,
        )


def test_unknown_rejection_order_id_is_rejected(
    tmp_path,
) -> None:
    """Unknown rejection must not manufacture execution record."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        KeyError,
        match="broker order ID 999",
    ):
        transport.handle_rejection(
            broker_order_id=999,
            reason="Rejected.",
        )


def test_unknown_ib_status_is_ignored(
    tmp_path,
) -> None:
    """Unknown status should fail safe without changing ledger."""

    transport, ledger = create_transport(
        tmp_path
    )

    result = transport.handle_order_status(
        broker_order_id=100,
        status="SomeFutureIBStatus",
        filled=0,
        remaining=5,
    )

    assert (
        result.outcome
        is IBStatusOutcome.IGNORED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.SUBMITTED
    )


@pytest.mark.parametrize(
    "invalid_order_id",
    [
        -1,
        True,
        1.5,
        "100",
        None,
    ],
)
def test_invalid_broker_order_id_is_rejected(
    tmp_path,
    invalid_order_id,
) -> None:
    """Broker order ID must be non-negative integer."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="'broker_order_id'",
    ):
        transport.handle_order_status(
            broker_order_id=invalid_order_id,
            status="Submitted",
            filled=0,
            remaining=5,
        )


@pytest.mark.parametrize(
    "invalid_status",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_status_is_rejected(
    tmp_path,
    invalid_status,
) -> None:
    """IB status must contain text."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="'status'",
    ):
        transport.handle_order_status(
            broker_order_id=100,
            status=invalid_status,
            filled=0,
            remaining=5,
        )


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("filled", -1),
        ("filled", True),
        ("filled", "NaN"),
        ("remaining", -1),
        ("remaining", True),
        ("remaining", "Infinity"),
    ],
)
def test_invalid_callback_quantities_are_rejected(
    tmp_path,
    field_name,
    invalid_value,
) -> None:
    """IB fill quantities must be finite and non-negative."""

    transport, _ = create_transport(
        tmp_path
    )

    filled = 0
    remaining = 5

    if field_name == "filled":
        filled = invalid_value
    else:
        remaining = invalid_value

    with pytest.raises(
        ValueError,
        match=field_name,
    ):
        transport.handle_order_status(
            broker_order_id=100,
            status="Submitted",
            filled=filled,
            remaining=remaining,
        )


def test_empty_rejection_reason_is_rejected(
    tmp_path,
) -> None:
    """Confirmed rejection requires an audit reason."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="'reason'",
    ):
        transport.handle_rejection(
            broker_order_id=100,
            reason="   ",
        )


def test_invalid_ledger_is_rejected() -> None:
    """Transport requires ExecutionLedger."""

    with pytest.raises(
        TypeError,
        match="'execution_ledger' must be an ExecutionLedger",
    ):
        IBOrderStatusTransport(
            object()  # type: ignore[arg-type]
        )