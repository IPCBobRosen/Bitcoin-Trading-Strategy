"""Tests for the BTS IB execution-details transport."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ibapi.contract import Contract
from ibapi.execution import Execution

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionStatus,
)
from app.ib_execution_details_transport import (
    IBExecutionDetailsOutcome,
    IBExecutionDetailsTransport,
)


def create_trade_request(
    *,
    event_id: str = "event-001",
    intent_value: str = "BUY_TO_OPEN",
    quantity: int = 5,
) -> TradeRequest:
    """Create deterministic BTS TradeRequest."""

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
        intent=TradeIntent(
            intent_value
        ),
        symbol="MBT",
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def create_submitted_ledger(
    tmp_path,
    *,
    broker_order_id: int = 100,
    intent_value: str = "BUY_TO_OPEN",
    quantity: int = 5,
) -> ExecutionLedger:
    """Create ledger containing one submitted execution."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )

    ledger.reserve(
        create_trade_request(
            intent_value=intent_value,
            quantity=quantity,
        )
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=broker_order_id,
    )

    return ledger


def create_contract(
    *,
    symbol: str = "MBT",
) -> Contract:
    """Create official IB Contract."""

    contract = Contract()

    contract.symbol = symbol
    contract.secType = "FUT"

    return contract


def create_execution(
    *,
    order_id: int = 100,
    exec_id: str = "exec-001",
    side: str = "BOT",
    shares=2,
    cum_qty=2,
) -> Execution:
    """Create official IB Execution object."""

    execution = Execution()

    execution.orderId = order_id
    execution.execId = exec_id
    execution.side = side
    execution.shares = shares
    execution.cumQty = cum_qty

    return execution


def create_transport(
    tmp_path,
    *,
    intent_value: str = "BUY_TO_OPEN",
    quantity: int = 5,
):
    """Create transport and submitted ledger."""

    ledger = create_submitted_ledger(
        tmp_path,
        intent_value=intent_value,
        quantity=quantity,
    )

    transport = IBExecutionDetailsTransport(
        ledger
    )

    return transport, ledger


def test_transport_retains_ledger(
    tmp_path,
) -> None:
    """Transport should retain durable ledger."""

    transport, ledger = create_transport(
        tmp_path
    )

    assert (
        transport.execution_ledger
        is ledger
    )


def test_new_transport_has_no_processed_executions(
    tmp_path,
) -> None:
    """Execution table should begin empty."""

    transport, _ = create_transport(
        tmp_path
    )

    assert (
        transport.processed_execution_count()
        == 0
    )


def test_partial_execution_marks_partially_filled(
    tmp_path,
) -> None:
    """cumQty below requested quantity means partial fill."""

    transport, ledger = create_transport(
        tmp_path
    )

    result = transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            shares=2,
            cum_qty=2,
        ),
    )

    assert (
        result.outcome
        is IBExecutionDetailsOutcome.UPDATED
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.PARTIALLY_FILLED
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.PARTIALLY_FILLED
    )


def test_full_execution_marks_filled(
    tmp_path,
) -> None:
    """cumQty equal to requested quantity means filled."""

    transport, _ = create_transport(
        tmp_path
    )

    result = transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            shares=5,
            cum_qty=5,
        ),
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.FILLED
    )

    assert result.execution_record.terminal is True


def test_execution_can_fill_directly_from_submitted(
    tmp_path,
) -> None:
    """execDetails may prove fill without orderStatus acknowledgement."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            shares=5,
            cum_qty=5,
        ),
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.FILLED
    )


def test_second_execution_completes_partial_order(
    tmp_path,
) -> None:
    """Later partial execution may complete total quantity."""

    transport, _ = create_transport(
        tmp_path
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-001",
            shares=2,
            cum_qty=2,
        ),
    )

    result = transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-002",
            shares=3,
            cum_qty=5,
        ),
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.FILLED
    )


def test_duplicate_exec_id_is_idempotent(
    tmp_path,
) -> None:
    """Repeated IB execId must not be processed twice."""

    transport, ledger = create_transport(
        tmp_path
    )

    execution = create_execution(
        exec_id="exec-001",
        shares=2,
        cum_qty=2,
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=execution,
    )

    history_before = ledger.history(
        "event-001"
    )

    result = transport.handle_execution(
        contract=create_contract(),
        execution=execution,
    )

    history_after = ledger.history(
        "event-001"
    )

    assert (
        result.outcome
        is IBExecutionDetailsOutcome.DUPLICATE
    )

    assert history_after == history_before


def test_exec_id_is_durable_across_restart(
    tmp_path,
) -> None:
    """Processed execution IDs must survive BTS restart."""

    database_path = (
        tmp_path
        / "execution_ledger.db"
    )

    ledger = ExecutionLedger(
        database_path
    )

    ledger.reserve(
        create_trade_request()
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=100,
    )

    first = IBExecutionDetailsTransport(
        ledger
    )

    execution = create_execution()

    first.handle_execution(
        contract=create_contract(),
        execution=execution,
    )

    restarted = IBExecutionDetailsTransport(
        ExecutionLedger(
            database_path
        )
    )

    result = restarted.handle_execution(
        contract=create_contract(),
        execution=execution,
    )

    assert (
        result.outcome
        is IBExecutionDetailsOutcome.DUPLICATE
    )


def test_contains_execution_returns_true(
    tmp_path,
) -> None:
    """Recorded execId should be discoverable."""

    transport, _ = create_transport(
        tmp_path
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-555"
        ),
    )

    assert (
        transport.contains_execution(
            "exec-555"
        )
        is True
    )


def test_different_exec_ids_are_recorded(
    tmp_path,
) -> None:
    """Separate partial fills should each retain execId."""

    transport, _ = create_transport(
        tmp_path
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-001",
            shares=2,
            cum_qty=2,
        ),
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-002",
            shares=2,
            cum_qty=4,
        ),
    )

    assert (
        transport.processed_execution_count()
        == 2
    )


def test_second_partial_fill_does_not_duplicate_state_transition(
    tmp_path,
) -> None:
    """Additional partial execution should not add redundant state."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-001",
            shares=2,
            cum_qty=2,
        ),
    )

    history_before = ledger.history(
        "event-001"
    )

    result = transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-002",
            shares=1,
            cum_qty=3,
        ),
    )

    history_after = ledger.history(
        "event-001"
    )

    assert (
        result.outcome
        is IBExecutionDetailsOutcome.NO_CHANGE
    )

    assert history_after == history_before


@pytest.mark.parametrize(
    (
        "intent_value",
        "side",
    ),
    [
        ("BUY_TO_OPEN", "BOT"),
        ("BUY_TO_CLOSE", "BOT"),
        ("SELL_TO_OPEN", "SLD"),
        ("SELL_TO_CLOSE", "SLD"),
    ],
)
def test_execution_side_matches_all_bts_intents(
    tmp_path,
    intent_value: str,
    side: str,
) -> None:
    """IB BOT/SLD side should match all four BTS intents."""

    transport, _ = create_transport(
        tmp_path,
        intent_value=intent_value,
    )

    result = transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            side=side,
            shares=5,
            cum_qty=5,
        ),
    )

    assert (
        result.execution_record.status
        is ExecutionStatus.FILLED
    )


def test_wrong_execution_side_is_rejected(
    tmp_path,
) -> None:
    """IB side mismatch must fail safe."""

    transport, ledger = create_transport(
        tmp_path,
        intent_value="BUY_TO_OPEN",
    )

    with pytest.raises(
        RuntimeError,
        match="does not match",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=create_execution(
                side="SLD",
            ),
        )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.SUBMITTED
    )


def test_wrong_contract_symbol_is_rejected(
    tmp_path,
) -> None:
    """Execution for wrong symbol must not update BTS."""

    transport, ledger = create_transport(
        tmp_path
    )

    with pytest.raises(
        RuntimeError,
        match="does not match",
    ):
        transport.handle_execution(
            contract=create_contract(
                symbol="MES"
            ),
            execution=create_execution(),
        )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.SUBMITTED
    )


def test_unknown_broker_order_id_is_rejected(
    tmp_path,
) -> None:
    """Execution must belong to durable BTS order."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        KeyError,
        match="broker order ID 999",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=create_execution(
                order_id=999,
            ),
        )


def test_cumulative_quantity_over_order_size_is_rejected(
    tmp_path,
) -> None:
    """IB fill larger than BTS order requires intervention."""

    transport, ledger = create_transport(
        tmp_path,
        quantity=5,
    )

    with pytest.raises(
        RuntimeError,
        match="exceeds BTS requested quantity",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=create_execution(
                shares=6,
                cum_qty=6,
            ),
        )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.SUBMITTED
    )


def test_shares_cannot_exceed_cumulative_quantity(
    tmp_path,
) -> None:
    """Individual fill cannot exceed reported cumulative fill."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="shares cannot exceed",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=create_execution(
                shares=4,
                cum_qty=2,
            ),
        )


def test_execution_after_cancelled_is_inconsistency(
    tmp_path,
) -> None:
    """Actual fill after BTS cancellation state requires intervention."""

    transport, ledger = create_transport(
        tmp_path
    )

    ledger.mark_cancelled(
        "event-001"
    )

    with pytest.raises(
        RuntimeError,
        match="after BTS recorded terminal state",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=create_execution(),
        )


def test_execution_after_rejected_is_inconsistency(
    tmp_path,
) -> None:
    """Actual fill after rejection state requires intervention."""

    transport, ledger = create_transport(
        tmp_path
    )

    ledger.mark_rejected(
        "event-001",
        reason="Rejected.",
    )

    with pytest.raises(
        RuntimeError,
        match="after BTS recorded terminal state",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=create_execution(),
        )


def test_new_exec_id_after_filled_is_recorded_without_regression(
    tmp_path,
) -> None:
    """Late execution information must not regress FILLED state."""

    transport, ledger = create_transport(
        tmp_path
    )

    transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-001",
            shares=5,
            cum_qty=5,
        ),
    )

    result = transport.handle_execution(
        contract=create_contract(),
        execution=create_execution(
            exec_id="exec-002",
            shares=1,
            cum_qty=5,
        ),
    )

    assert (
        result.outcome
        is IBExecutionDetailsOutcome.NO_CHANGE
    )

    assert (
        ledger.get("event-001").status
        is ExecutionStatus.FILLED
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
def test_invalid_order_id_is_rejected(
    tmp_path,
    invalid_order_id,
) -> None:
    """IB execution order ID must be valid integer."""

    transport, _ = create_transport(
        tmp_path
    )

    execution = create_execution()
    execution.orderId = invalid_order_id

    with pytest.raises(
        ValueError,
        match="'broker_order_id'",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=execution,
        )


@pytest.mark.parametrize(
    "invalid_exec_id",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_exec_id_is_rejected(
    tmp_path,
    invalid_exec_id,
) -> None:
    """IB execution requires usable execId."""

    transport, _ = create_transport(
        tmp_path
    )

    execution = create_execution()
    execution.execId = invalid_exec_id

    with pytest.raises(
        ValueError,
        match="'exec_id'",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=execution,
        )


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("shares", 0),
        ("shares", -1),
        ("shares", True),
        ("shares", "NaN"),
        ("cumQty", 0),
        ("cumQty", -1),
        ("cumQty", True),
        ("cumQty", "Infinity"),
    ],
)
def test_invalid_execution_quantities_are_rejected(
    tmp_path,
    field_name,
    invalid_value,
) -> None:
    """Execution quantities must be finite and positive."""

    transport, _ = create_transport(
        tmp_path
    )

    execution = create_execution()

    setattr(
        execution,
        field_name,
        invalid_value,
    )

    with pytest.raises(
        ValueError,
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=execution,
        )


def test_invalid_contract_is_rejected(
    tmp_path,
) -> None:
    """Transport requires official IB Contract."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        TypeError,
        match="'contract' must be an IB Contract",
    ):
        transport.handle_execution(
            contract=object(),  # type: ignore[arg-type]
            execution=create_execution(),
        )


def test_invalid_execution_is_rejected(
    tmp_path,
) -> None:
    """Transport requires official IB Execution."""

    transport, _ = create_transport(
        tmp_path
    )

    with pytest.raises(
        TypeError,
        match="'execution' must be an IB Execution",
    ):
        transport.handle_execution(
            contract=create_contract(),
            execution=object(),  # type: ignore[arg-type]
        )


def test_invalid_ledger_is_rejected() -> None:
    """Transport requires ExecutionLedger."""

    with pytest.raises(
        TypeError,
        match="'execution_ledger' must be an ExecutionLedger",
    ):
        IBExecutionDetailsTransport(
            object()  # type: ignore[arg-type]
        )