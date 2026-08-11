"""Tests for BTS Interactive Brokers error handling."""

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
from app.ib_error_handler import (
    IBErrorHandler,
    IBErrorResult,
    IBErrorSeverity,
)
from app.kill_switch import KillSwitch


def create_trade_request(
    *,
    event_id: str = "event-001",
    quantity: int = 1,
) -> TradeRequest:
    """Create a deterministic BTS TradeRequest."""

    return TradeRequest(
        event_id=event_id,
        signal_id="signal-001",
        timestamp=datetime(
            2026,
            8,
            11,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol="MBT",
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def create_submitted_ledger(
    tmp_path,
    *,
    event_id: str = "event-001",
    broker_order_id: int = 100,
) -> ExecutionLedger:
    """Create one durable submitted execution."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )

    ledger.reserve(
        create_trade_request(
            event_id=event_id
        )
    )

    ledger.mark_submitted(
        event_id,
        broker_order_id=broker_order_id,
    )

    return ledger


def create_handler(
    tmp_path,
):
    """Create handler, ledger, and kill switch."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )

    kill_switch = KillSwitch()

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=kill_switch,
    )

    return (
        handler,
        ledger,
        kill_switch,
    )


def test_handler_retains_dependencies(
    tmp_path,
) -> None:
    """Handler should retain BTS safety dependencies."""

    (
        handler,
        ledger,
        kill_switch,
    ) = create_handler(
        tmp_path
    )

    assert (
        handler.execution_ledger
        is ledger
    )

    assert (
        handler.kill_switch
        is kill_switch
    )


@pytest.mark.parametrize(
    "error_code",
    [
        2104,
        2106,
        2107,
        2108,
        2158,
    ],
)
def test_informational_codes_do_not_trip_kill_switch(
    tmp_path,
    error_code: int,
) -> None:
    """Normal IB notifications must not stop BTS."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    result = handler.handle(
        request_id=-1,
        error_code=error_code,
        message="IB informational notification.",
    )

    assert (
        result.severity
        is IBErrorSeverity.INFORMATION
    )

    assert result.trading_blocked is False
    assert kill_switch.active is False


@pytest.mark.parametrize(
    "error_code",
    [
        2100,
        2101,
        2102,
        2103,
        2105,
        2109,
        2137,
        2168,
        2169,
    ],
)
def test_warning_codes_do_not_automatically_trip_kill_switch(
    tmp_path,
    error_code: int,
) -> None:
    """Warnings should be surfaced without automatic emergency stop."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    result = handler.handle(
        request_id=-1,
        error_code=error_code,
        message="IB warning.",
    )

    assert (
        result.severity
        is IBErrorSeverity.WARNING
    )

    assert kill_switch.active is False


@pytest.mark.parametrize(
    "error_code",
    [
        1100,
        2110,
    ],
)
def test_connection_loss_trips_kill_switch(
    tmp_path,
    error_code: int,
) -> None:
    """Loss of IB server connectivity must block trading."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    result = handler.handle(
        request_id=-1,
        error_code=error_code,
        message="Connectivity lost.",
    )

    assert (
        result.severity
        is IBErrorSeverity.CONNECTION_LOST
    )

    assert result.trading_blocked is True
    assert kill_switch.active is True


def test_connection_loss_records_audit_reason(
    tmp_path,
) -> None:
    """Kill switch should retain the IB code and message."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    handler.handle(
        request_id=-1,
        error_code=1100,
        message="Connectivity between IB and TWS lost.",
    )

    assert kill_switch.reason is not None

    assert "1100" in kill_switch.reason

    assert (
        "Connectivity between IB and TWS lost."
        in kill_switch.reason
    )


@pytest.mark.parametrize(
    "error_code",
    [
        1101,
        1102,
    ],
)
def test_restoration_is_classified_correctly(
    tmp_path,
    error_code: int,
) -> None:
    """IB restoration should be recognized."""

    handler, _, _ = create_handler(
        tmp_path
    )

    result = handler.handle(
        request_id=-1,
        error_code=error_code,
        message="Connectivity restored.",
    )

    assert (
        result.severity
        is IBErrorSeverity.CONNECTION_RESTORED
    )


def test_restoration_does_not_reset_existing_kill_switch(
    tmp_path,
) -> None:
    """Reconnect must not silently resume trading."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    handler.handle(
        request_id=-1,
        error_code=1100,
        message="Connectivity lost.",
    )

    assert kill_switch.active is True

    result = handler.handle(
        request_id=-1,
        error_code=1102,
        message="Connectivity restored.",
    )

    assert result.trading_blocked is True
    assert kill_switch.active is True


@pytest.mark.parametrize(
    "error_code",
    [
        100,
        103,
        1300,
        502,
        503,
        504,
        507,
        10015,
    ],
)
def test_critical_codes_trip_kill_switch(
    tmp_path,
    error_code: int,
) -> None:
    """Critical IB API failures should emergency-stop BTS."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    result = handler.handle(
        request_id=-1,
        error_code=error_code,
        message="Critical IB failure.",
    )

    assert (
        result.severity
        is IBErrorSeverity.CRITICAL
    )

    assert result.trading_blocked is True
    assert kill_switch.active is True


def test_first_critical_error_reason_is_preserved(
    tmp_path,
) -> None:
    """KillSwitch should preserve the original IB emergency cause."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    handler.handle(
        request_id=-1,
        error_code=1100,
        message="First failure.",
    )

    handler.handle(
        request_id=-1,
        error_code=1300,
        message="Second failure.",
    )

    assert (
        kill_switch.reason
        == "IB error 1100: First failure."
    )


def test_known_order_rejection_updates_ledger(
    tmp_path,
) -> None:
    """IB 201 should mark our submitted order REJECTED."""

    ledger = create_submitted_ledger(
        tmp_path,
        broker_order_id=100,
    )

    kill_switch = KillSwitch()

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=kill_switch,
    )

    result = handler.handle(
        request_id=100,
        error_code=201,
        message="Order rejected - insufficient margin.",
    )

    assert (
        result.severity
        is IBErrorSeverity.ORDER_REJECTED
    )

    assert result.execution_record is not None

    assert (
        result.execution_record.status
        is ExecutionStatus.REJECTED
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.REJECTED
    )


def test_order_rejection_persists_ib_reason(
    tmp_path,
) -> None:
    """Ledger should retain actual IB rejection reason."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=KillSwitch(),
    )

    handler.handle(
        request_id=100,
        error_code=201,
        message="Order rejected by broker.",
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None
    assert record.reason is not None

    assert "201" in record.reason
    assert "Order rejected by broker." in record.reason


def test_order_rejection_does_not_trip_kill_switch(
    tmp_path,
) -> None:
    """One normal broker rejection is not a system emergency."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    kill_switch = KillSwitch()

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=kill_switch,
    )

    result = handler.handle(
        request_id=100,
        error_code=201,
        message="Order rejected.",
    )

    assert result.trading_blocked is False
    assert kill_switch.active is False


def test_duplicate_order_rejection_is_idempotent(
    tmp_path,
) -> None:
    """Repeated IB 201 must not create duplicate transitions."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=KillSwitch(),
    )

    handler.handle(
        request_id=100,
        error_code=201,
        message="Order rejected.",
    )

    history_before = ledger.history(
        "event-001"
    )

    result = handler.handle(
        request_id=100,
        error_code=201,
        message="Order rejected again.",
    )

    history_after = ledger.history(
        "event-001"
    )

    assert history_after == history_before

    assert (
        result.execution_record.status
        is ExecutionStatus.REJECTED
    )


def test_external_order_rejection_does_not_create_bts_record(
    tmp_path,
) -> None:
    """Rejection for non-BTS order must not corrupt ledger."""

    handler, ledger, _ = create_handler(
        tmp_path
    )

    result = handler.handle(
        request_id=999,
        error_code=201,
        message="External order rejected.",
    )

    assert result.execution_record is None

    assert ledger.all_records() == ()


def test_known_order_cancellation_updates_ledger(
    tmp_path,
) -> None:
    """IB 202 should mark our submitted order CANCELLED."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=KillSwitch(),
    )

    result = handler.handle(
        request_id=100,
        error_code=202,
        message="Order cancelled.",
    )

    assert (
        result.severity
        is IBErrorSeverity.ORDER_CANCELLED
    )

    assert result.execution_record is not None

    assert (
        result.execution_record.status
        is ExecutionStatus.CANCELLED
    )


def test_order_cancellation_does_not_trip_kill_switch(
    tmp_path,
) -> None:
    """Normal broker cancellation is not system emergency."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    kill_switch = KillSwitch()

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=kill_switch,
    )

    handler.handle(
        request_id=100,
        error_code=202,
        message="Order cancelled.",
    )

    assert kill_switch.active is False


def test_duplicate_order_cancellation_is_idempotent(
    tmp_path,
) -> None:
    """Repeated cancellation should not duplicate history."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=KillSwitch(),
    )

    handler.handle(
        request_id=100,
        error_code=202,
        message="Order cancelled.",
    )

    history_before = ledger.history(
        "event-001"
    )

    handler.handle(
        request_id=100,
        error_code=202,
        message="Order cancelled again.",
    )

    history_after = ledger.history(
        "event-001"
    )

    assert history_after == history_before


def test_external_order_cancellation_does_not_create_record(
    tmp_path,
) -> None:
    """Cancellation for unrelated IB order should be harmless."""

    handler, ledger, _ = create_handler(
        tmp_path
    )

    result = handler.handle(
        request_id=999,
        error_code=202,
        message="External cancellation.",
    )

    assert result.execution_record is None
    assert ledger.all_records() == ()


def test_rejection_after_fill_does_not_regress_ledger(
    tmp_path,
) -> None:
    """Late IB rejection must not overwrite confirmed fill."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    ledger.mark_filled(
        "event-001"
    )

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=KillSwitch(),
    )

    result = handler.handle(
        request_id=100,
        error_code=201,
        message="Late rejection.",
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.FILLED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.FILLED
    )


def test_cancellation_after_fill_does_not_regress_ledger(
    tmp_path,
) -> None:
    """Late cancellation must not overwrite confirmed fill."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    ledger.mark_filled(
        "event-001"
    )

    handler = IBErrorHandler(
        execution_ledger=ledger,
        kill_switch=KillSwitch(),
    )

    result = handler.handle(
        request_id=100,
        error_code=202,
        message="Late cancellation.",
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.FILLED
    )


def test_unknown_error_code_is_not_automatic_kill(
    tmp_path,
) -> None:
    """Unknown codes should fail conservatively without inventing action."""

    handler, _, kill_switch = (
        create_handler(
            tmp_path
        )
    )

    result = handler.handle(
        request_id=-1,
        error_code=987654,
        message="Future IB message.",
    )

    assert (
        result.severity
        is IBErrorSeverity.UNKNOWN
    )

    assert kill_switch.active is False


def test_result_is_immutable(
    tmp_path,
) -> None:
    """IB error results must not be mutable."""

    handler, _, _ = create_handler(
        tmp_path
    )

    result = handler.handle(
        request_id=-1,
        error_code=2104,
        message="Market data farm connection is OK.",
    )

    assert isinstance(
        result,
        IBErrorResult,
    )

    with pytest.raises(
        AttributeError,
    ):
        result.error_code = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid_request_id",
    [
        -2,
        True,
        1.5,
        "100",
        None,
    ],
)
def test_invalid_request_id_is_rejected(
    tmp_path,
    invalid_request_id,
) -> None:
    """IB request ID permits -1 or non-negative integers only."""

    handler, _, _ = create_handler(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="'request_id'",
    ):
        handler.handle(
            request_id=invalid_request_id,
            error_code=2104,
            message="Message.",
        )


@pytest.mark.parametrize(
    "invalid_error_code",
    [
        -1,
        True,
        1.5,
        "201",
        None,
    ],
)
def test_invalid_error_code_is_rejected(
    tmp_path,
    invalid_error_code,
) -> None:
    """IB error code must be a non-negative integer."""

    handler, _, _ = create_handler(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="'error_code'",
    ):
        handler.handle(
            request_id=-1,
            error_code=invalid_error_code,
            message="Message.",
        )


@pytest.mark.parametrize(
    "invalid_message",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_message_is_rejected(
    tmp_path,
    invalid_message,
) -> None:
    """IB error callback requires meaningful message text."""

    handler, _, _ = create_handler(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="'message'",
    ):
        handler.handle(
            request_id=-1,
            error_code=2104,
            message=invalid_message,
        )


def test_invalid_ledger_is_rejected(
    tmp_path,
) -> None:
    """Handler requires ExecutionLedger."""

    with pytest.raises(
        TypeError,
        match="'execution_ledger'",
    ):
        IBErrorHandler(
            execution_ledger=object(),  # type: ignore[arg-type]
            kill_switch=KillSwitch(),
        )


def test_invalid_kill_switch_is_rejected(
    tmp_path,
) -> None:
    """Handler requires KillSwitch."""

    ledger = ExecutionLedger(
        tmp_path
        / "ledger.db"
    )

    with pytest.raises(
        TypeError,
        match="'kill_switch'",
    ):
        IBErrorHandler(
            execution_ledger=ledger,
            kill_switch=object(),  # type: ignore[arg-type]
        )