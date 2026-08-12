"""Offline tests for working-order disconnect recovery."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

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
)
from app.ib_order_factory import IBOrderFactory

from scripts.test_ib_paper_working_order_disconnect import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    LIMIT_PRICE,
    QUANTITY,
    IBWorkingOrderDisconnectResult,
    build_trade_request,
    print_result,
    wait_for_working_order,
)


def create_request() -> TradeRequest:
    """Create deterministic working-order request."""

    return TradeRequest(
        event_id="working-event-001",
        signal_id="working-signal-001",
        timestamp=datetime(
            2026,
            8,
            12,
            16,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol="MBT",
        quantity=1,
        stop_loss_points=Decimal("500"),
    )


def create_submitted_ledger(
    tmp_path,
) -> ExecutionLedger:
    """Create one submitted execution record."""

    ledger = ExecutionLedger(
        tmp_path
        / "ledger.db"
    )

    request = create_request()

    ledger.reserve(
        request
    )

    ledger.mark_submitted(
        request.event_id,
        broker_order_id=10,
    )

    return ledger


def create_success_result() -> IBWorkingOrderDisconnectResult:
    """Create one completely successful test result."""

    return IBWorkingOrderDisconnectResult(
        event_id="working-event-001",
        broker_order_id=10,
        initial_position_count=0,
        working_status=ExecutionStatus.ACKNOWLEDGED,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        blocked_after_restore=True,
        position_before_cancel=0,
        cancelled_status=ExecutionStatus.CANCELLED,
        final_position=0,
        operator_reset_confirmed=True,
        final_kill_switch_active=False,
        final_readiness=True,
    )


def test_limit_price_is_63250() -> None:
    """Live test price should remain hard-coded."""

    assert LIMIT_PRICE == Decimal("63250")


def test_quantity_is_exactly_one() -> None:
    """Live outage test must use one MBT."""

    assert QUANTITY == 1


def test_contract_is_august_2026() -> None:
    """Working test should use verified August expiry."""

    assert CONTRACT_MONTH == "20260828"


def test_trade_request_is_staging_buy_to_open() -> None:
    """Working order must be paper BUY_TO_OPEN."""

    request = build_trade_request(
        event_id="event-001"
    )

    assert request.environment is Environment.STAGING
    assert request.intent is TradeIntent.BUY_TO_OPEN
    assert request.symbol == "MBT"
    assert request.quantity == 1


def test_empty_event_id_is_rejected() -> None:
    """Audit event ID is mandatory."""

    with pytest.raises(
        ValueError,
        match="'event_id'",
    ):
        build_trade_request(
            event_id=" "
        )


def test_limit_factory_builds_exact_order() -> None:
    """Production factory should build BUY 1 at 63250."""

    factory = IBOrderFactory(
        exchange="CME",
        currency="USD",
        trading_class="MBT",
        order_type="LMT",
        time_in_force="DAY",
        transmit=True,
        limit_price=LIMIT_PRICE,
    )

    package = factory.create(
        create_request(),
        contract_month=CONTRACT_MONTH,
    )

    assert package.order.action == "BUY"
    assert package.order.totalQuantity == 1
    assert package.order.orderType == "LMT"
    assert package.order.lmtPrice == 63250.0
    assert package.order.transmit is True


def test_submitted_becomes_acknowledged(
    tmp_path,
) -> None:
    """IB Submitted should produce durable ACKNOWLEDGED."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    transport = IBOrderStatusTransport(
        ledger
    )

    transport.handle_order_status(
        broker_order_id=10,
        status="Submitted",
        filled=0,
        remaining=1,
    )

    record = ledger.get(
        "working-event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.ACKNOWLEDGED
    )


def test_acknowledged_can_be_cancelled(
    tmp_path,
) -> None:
    """Original working order should transition to CANCELLED."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    transport = IBOrderStatusTransport(
        ledger
    )

    transport.handle_order_status(
        broker_order_id=10,
        status="Submitted",
        filled=0,
        remaining=1,
    )

    transport.handle_order_status(
        broker_order_id=10,
        status="Cancelled",
        filled=0,
        remaining=1,
    )

    record = ledger.get(
        "working-event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.CANCELLED
    )


def test_api_cancelled_maps_to_cancelled(
    tmp_path,
) -> None:
    """IB ApiCancelled should also terminate the order."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    transport = IBOrderStatusTransport(
        ledger
    )

    transport.handle_order_status(
        broker_order_id=10,
        status="Submitted",
        filled=0,
        remaining=1,
    )

    transport.handle_order_status(
        broker_order_id=10,
        status="ApiCancelled",
        filled=0,
        remaining=1,
    )

    record = ledger.get(
        "working-event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.CANCELLED
    )


def test_partial_fill_is_not_working_success(
    tmp_path,
) -> None:
    """Any partial fill must stop the outage procedure."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    transport = IBOrderStatusTransport(
        ledger
    )

    transport.handle_order_status(
        broker_order_id=10,
        status="Submitted",
        filled="0.5",
        remaining="0.5",
    )

    with pytest.raises(
        RuntimeError,
        match="did not remain safely working",
    ):
        wait_for_working_order(
            ledger=ledger,
            event_id="working-event-001",
        )


def test_full_fill_is_not_working_success(
    tmp_path,
) -> None:
    """Unexpected full fill must stop the test."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    transport = IBOrderStatusTransport(
        ledger
    )

    transport.handle_order_status(
        broker_order_id=10,
        status="Filled",
        filled=1,
        remaining=0,
    )

    with pytest.raises(
        RuntimeError,
        match="did not remain safely working",
    ):
        wait_for_working_order(
            ledger=ledger,
            event_id="working-event-001",
        )


def test_success_result_is_successful() -> None:
    """Complete safe lifecycle should pass."""

    assert (
        create_success_result().successful
        is True
    )


def test_wrong_working_status_fails_result() -> None:
    """The outage may begin only from ACKNOWLEDGED."""

    result = create_success_result()

    changed = IBWorkingOrderDisconnectResult(
        event_id=result.event_id,
        broker_order_id=result.broker_order_id,
        initial_position_count=0,
        working_status=ExecutionStatus.FILLED,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        blocked_after_restore=True,
        position_before_cancel=0,
        cancelled_status=ExecutionStatus.CANCELLED,
        final_position=0,
        operator_reset_confirmed=True,
        final_kill_switch_active=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_position_before_cancel_must_be_flat() -> None:
    """A fill during outage must invalidate success."""

    result = create_success_result()

    changed = IBWorkingOrderDisconnectResult(
        event_id=result.event_id,
        broker_order_id=result.broker_order_id,
        initial_position_count=0,
        working_status=ExecutionStatus.ACKNOWLEDGED,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        blocked_after_restore=True,
        position_before_cancel=1,
        cancelled_status=ExecutionStatus.CANCELLED,
        final_position=0,
        operator_reset_confirmed=True,
        final_kill_switch_active=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_cancelled_status_is_required() -> None:
    """Original broker order must be confirmed cancelled."""

    result = create_success_result()

    changed = IBWorkingOrderDisconnectResult(
        event_id=result.event_id,
        broker_order_id=result.broker_order_id,
        initial_position_count=0,
        working_status=ExecutionStatus.ACKNOWLEDGED,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        blocked_after_restore=True,
        position_before_cancel=0,
        cancelled_status=ExecutionStatus.ACKNOWLEDGED,
        final_position=0,
        operator_reset_confirmed=True,
        final_kill_switch_active=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_final_position_must_be_flat() -> None:
    """Cancellation alone is not enough if exposure exists."""

    result = create_success_result()

    changed = IBWorkingOrderDisconnectResult(
        event_id=result.event_id,
        broker_order_id=result.broker_order_id,
        initial_position_count=0,
        working_status=ExecutionStatus.ACKNOWLEDGED,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        blocked_after_restore=True,
        position_before_cancel=0,
        cancelled_status=ExecutionStatus.CANCELLED,
        final_position=1,
        operator_reset_confirmed=True,
        final_kill_switch_active=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_result_is_immutable() -> None:
    """Live test result should not mutate."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_position = 1  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful result should clearly report PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "without resubmission" in output


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_exact_arming_argument_is_present() -> None:
    """Working-order test should require deliberate arming."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-working-order-test"
    )


def test_script_contains_no_second_submit_call() -> None:
    """Source should contain only one execution-client submit call."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_working_order_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        source.count(
            "execution_client.submit("
        )
        == 1
    )


def test_script_cancels_existing_order_id() -> None:
    """Recovery should cancel rather than resubmit."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_working_order_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "app.cancelOrder(" in source
    assert "OrderCancel()" in source