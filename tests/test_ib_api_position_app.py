"""Tests for the Interactive Brokers BTS API application."""

from datetime import datetime, timezone
from decimal import Decimal

from unittest.mock import patch

import pytest

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.execution import Execution
from ibapi.order import Order
from ibapi.order_state import OrderState
from ibapi.wrapper import EWrapper

from app.broker_position_adapter import RawBrokerPosition
from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionStatus,
)
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_execution_details_transport import (
    IBExecutionDetailsTransport,
)
from app.ib_order_id_allocator import IBOrderIdAllocator
from app.ib_order_status_transport import (
    IBOrderStatusTransport,
)


def create_contract(
    *,
    symbol: str = "MBT",
    local_symbol: str = "MBTQ6",
    con_id: int = 123456,
) -> Contract:
    """Create a minimal official IBKR Contract."""

    contract = Contract()

    contract.symbol = symbol
    contract.localSymbol = local_symbol
    contract.conId = con_id
    contract.tradingClass = "MBT"

    contract.lastTradeDateOrContractMonth = (
        "20260828"
    )

    return contract


def create_trade_request(
    *,
    event_id: str = "event-001",
    signal_id: str = "signal-001",
    intent_value: str = "BUY_TO_OPEN",
    quantity: int = 5,
) -> TradeRequest:
    """Create a deterministic BTS TradeRequest."""

    return TradeRequest(
        event_id=event_id,
        signal_id=signal_id,
        timestamp=datetime(
            2026,
            8,
            11,
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
    quantity: int = 5,
) -> ExecutionLedger:
    """Create one durable submitted BTS execution."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )

    ledger.reserve(
        create_trade_request(
            quantity=quantity
        )
    )

    ledger.mark_submitted(
        "event-001",
        broker_order_id=broker_order_id,
    )

    return ledger


def create_execution(
    *,
    order_id: int = 100,
    exec_id: str = "exec-001",
    shares=5,
    cum_qty=5,
    side: str = "BOT",
) -> Execution:
    """Create an official IB execution callback object."""

    execution = Execution()

    execution.orderId = order_id
    execution.execId = exec_id
    execution.shares = shares
    execution.cumQty = cum_qty
    execution.side = side

    return execution


def call_order_status(
    app: IBApiPositionApp,
    *,
    order_id: int = 100,
    status: str = "Submitted",
    filled=0,
    remaining=5,
) -> None:
    """Invoke the complete official IB orderStatus signature."""

    app.orderStatus(
        orderId=order_id,
        status=status,
        filled=filled,
        remaining=remaining,
        avgFillPrice=0.0,
        permId=1,
        parentId=0,
        lastFillPrice=0.0,
        clientId=1,
        whyHeld="",
        mktCapPrice=0.0,
    )


def test_app_is_ib_wrapper_and_client() -> None:
    """IB API app should use official EWrapper/EClient classes."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    assert isinstance(
        app,
        EWrapper,
    )

    assert isinstance(
        app,
        EClient,
    )


def test_invalid_broker_client_is_rejected() -> None:
    """IB API app requires an IBBrokerClient."""

    with pytest.raises(
        TypeError,
        match="'broker_client' must be an IBBrokerClient",
    ):
        IBApiPositionApp(
            object()  # type: ignore[arg-type]
        )


def test_invalid_execution_ledger_is_rejected() -> None:
    """Execution ledger must have the correct type."""

    with pytest.raises(
        TypeError,
        match="'execution_ledger'",
    ):
        IBApiPositionApp(
            IBBrokerClient(),
            execution_ledger=object(),  # type: ignore[arg-type]
        )


def test_invalid_order_id_allocator_is_rejected() -> None:
    """Order-ID allocator must have the correct type."""

    with pytest.raises(
        TypeError,
        match="'order_id_allocator'",
    ):
        IBApiPositionApp(
            IBBrokerClient(),
            order_id_allocator=object(),  # type: ignore[arg-type]
        )


def test_app_retains_broker_client() -> None:
    """App should retain the BTS broker client."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    assert app.broker_client is broker_client


def test_app_creates_order_id_allocator_by_default() -> None:
    """Every IB app should own an order-ID allocator."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    assert isinstance(
        app.order_id_allocator,
        IBOrderIdAllocator,
    )


def test_supplied_order_id_allocator_is_retained() -> None:
    """App should use an explicitly supplied allocator."""

    allocator = IBOrderIdAllocator()

    app = IBApiPositionApp(
        IBBrokerClient(),
        order_id_allocator=allocator,
    )

    assert app.order_id_allocator is allocator


def test_app_without_ledger_has_no_execution_transports() -> None:
    """Position-only configuration should remain supported."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    assert app.execution_ledger is None
    assert app.order_status_transport is None
    assert app.execution_details_transport is None


def test_app_with_ledger_creates_execution_transports(
    tmp_path,
) -> None:
    """Configured ledger should enable order callback transports."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    assert app.execution_ledger is ledger

    assert isinstance(
        app.order_status_transport,
        IBOrderStatusTransport,
    )

    assert isinstance(
        app.execution_details_transport,
        IBExecutionDetailsTransport,
    )


def test_new_app_is_not_api_ready() -> None:
    """New IB app should not be handshake-ready."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    assert app.api_ready.ready is False

    assert (
        app.api_ready.next_valid_order_id
        is None
    )


def test_new_app_order_allocator_is_not_initialized() -> None:
    """Allocator should wait for nextValidId."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    assert (
        app.order_id_allocator.initialized
        is False
    )


def test_next_valid_id_marks_api_ready() -> None:
    """IB nextValidId should mark handshake readiness."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    assert app.api_ready.ready is True

    assert (
        app.api_ready.next_valid_order_id
        == 100
    )


def test_next_valid_id_initializes_allocator() -> None:
    """Same callback should initialize order allocation."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    assert (
        app.order_id_allocator.initialized
        is True
    )

    assert (
        app.order_id_allocator.next_order_id
        == 100
    )


def test_invalid_next_valid_id_is_rejected() -> None:
    """Invalid IB order IDs must not mark readiness."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    with pytest.raises(
        ValueError,
        match="'order_id' must be a non-negative integer",
    ):
        app.nextValidId(
            -1
        )

    assert app.api_ready.ready is False

    assert (
        app.order_id_allocator.initialized
        is False
    )


def test_open_order_advances_order_id_floor() -> None:
    """openOrder should protect against order-ID reuse."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    app.openOrder(
        orderId=150,
        contract=create_contract(),
        order=Order(),
        orderState=OrderState(),
    )

    assert (
        app.order_id_allocator.next_order_id
        == 151
    )


def test_order_status_advances_order_id_floor_without_ledger() -> None:
    """orderStatus IDs matter even without execution tracking."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    call_order_status(
        app,
        order_id=175,
    )

    assert (
        app.order_id_allocator.next_order_id
        == 176
    )


def test_known_order_status_flows_into_execution_ledger(
    tmp_path,
) -> None:
    """Known BTS orderStatus callback should update ledger."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    app.nextValidId(
        200
    )

    call_order_status(
        app,
        order_id=100,
        status="Submitted",
        filled=0,
        remaining=5,
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.ACKNOWLEDGED
    )


def test_partial_order_status_flows_into_execution_ledger(
    tmp_path,
) -> None:
    """Partial fill callback should update durable BTS state."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    call_order_status(
        app,
        order_id=100,
        status="Submitted",
        filled=2,
        remaining=3,
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.PARTIALLY_FILLED
    )


def test_filled_order_status_flows_into_execution_ledger(
    tmp_path,
) -> None:
    """Filled status should reach durable BTS ledger."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    call_order_status(
        app,
        order_id=100,
        status="Filled",
        filled=5,
        remaining=0,
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.FILLED
    )


def test_unknown_order_status_is_ignored_by_bts_ledger(
    tmp_path,
) -> None:
    """External IB orders must not manufacture BTS records."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    app.nextValidId(
        100
    )

    call_order_status(
        app,
        order_id=500,
        status="Submitted",
        filled=0,
        remaining=1,
    )

    assert len(
        ledger.all_records()
    ) == 1

    assert (
        app.order_id_allocator.next_order_id
        == 501
    )


def test_exec_details_advances_order_id_floor_without_ledger() -> None:
    """execDetails order IDs should protect allocation floor."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    app.execDetails(
        reqId=1,
        contract=create_contract(),
        execution=create_execution(
            order_id=300,
        ),
    )

    assert (
        app.order_id_allocator.next_order_id
        == 301
    )


def test_known_execution_details_flow_into_ledger(
    tmp_path,
) -> None:
    """Known execDetails fill should update durable BTS state."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    app.execDetails(
        reqId=1,
        contract=create_contract(),
        execution=create_execution(
            order_id=100,
            exec_id="exec-001",
            shares=5,
            cum_qty=5,
        ),
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.FILLED
    )

    assert (
        app.execution_details_transport
        is not None
    )

    assert (
        app.execution_details_transport.contains_execution(
            "exec-001"
        )
        is True
    )


def test_partial_execution_details_flow_into_ledger(
    tmp_path,
) -> None:
    """Partial execDetails should update durable BTS state."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    app.execDetails(
        reqId=1,
        contract=create_contract(),
        execution=create_execution(
            order_id=100,
            exec_id="exec-partial",
            shares=2,
            cum_qty=2,
        ),
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.PARTIALLY_FILLED
    )


def test_unknown_execution_details_are_ignored_by_bts_ledger(
    tmp_path,
) -> None:
    """External IB executions must not manufacture BTS records."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    app.nextValidId(
        100
    )

    app.execDetails(
        reqId=1,
        contract=create_contract(),
        execution=create_execution(
            order_id=700,
            exec_id="external-exec",
        ),
    )

    assert len(
        ledger.all_records()
    ) == 1

    assert (
        app.order_id_allocator.next_order_id
        == 701
    )


def test_duplicate_execution_details_remain_idempotent(
    tmp_path,
) -> None:
    """Repeated execDetails callback must remain safe."""

    ledger = create_submitted_ledger(
        tmp_path
    )

    app = IBApiPositionApp(
        IBBrokerClient(),
        execution_ledger=ledger,
    )

    execution = create_execution(
        order_id=100,
        exec_id="exec-duplicate",
        shares=2,
        cum_qty=2,
    )

    app.execDetails(
        reqId=1,
        contract=create_contract(),
        execution=execution,
    )

    history_before = ledger.history(
        "event-001"
    )

    app.execDetails(
        reqId=1,
        contract=create_contract(),
        execution=execution,
    )

    history_after = ledger.history(
        "event-001"
    )

    assert history_after == history_before


def test_position_request_starts_snapshot() -> None:
    """Requesting positions should begin BTS snapshot collection."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ) as req_positions:
        app.request_position_snapshot()

    assert (
        broker_client.snapshot_in_progress
        is True
    )

    assert (
        broker_client.snapshot_complete
        is False
    )

    assert app.position_request_active is True

    req_positions.assert_called_once_with()


def test_duplicate_position_request_is_rejected() -> None:
    """Only one position subscription should be active."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

        with pytest.raises(
            RuntimeError,
            match="already active",
        ):
            app.request_position_snapshot()


def test_position_callback_flows_into_broker_client() -> None:
    """Official IB position callback should reach BTS."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(),
        position=Decimal("2"),
        avgCost=65000.0,
    )

    app.positionEnd()

    assert broker_client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBTQ6",
            quantity=2,
            signal_id=None,
        ),
    )


def test_short_position_callback_is_preserved() -> None:
    """Negative IB position should remain signed."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(),
        position=Decimal("-3"),
        avgCost=64000.0,
    )

    app.positionEnd()

    assert broker_client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBTQ6",
            quantity=-3,
            signal_id=None,
        ),
    )


def test_position_end_completes_snapshot() -> None:
    """positionEnd should mark initial snapshot complete."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.positionEnd()

    assert (
        broker_client.snapshot_in_progress
        is False
    )

    assert (
        broker_client.snapshot_complete
        is True
    )


def test_cancel_position_updates_calls_ib_api() -> None:
    """Active IB position subscription should be cancelable."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    with patch.object(
        app,
        "cancelPositions",
    ) as cancel_positions:
        app.cancel_position_updates()

    cancel_positions.assert_called_once_with()

    assert app.position_request_active is False


def test_cancel_when_not_active_is_noop() -> None:
    """Cancel should do nothing without active subscription."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "cancelPositions",
    ) as cancel_positions:
        app.cancel_position_updates()

    cancel_positions.assert_not_called()


def test_connection_closed_resets_request_state() -> None:
    """IB connection loss should clear position request flag."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    assert app.position_request_active is True

    app.connectionClosed()

    assert app.position_request_active is False


def test_connection_closed_resets_api_readiness() -> None:
    """Connection loss should invalidate handshake readiness."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    assert app.api_ready.ready is True

    app.connectionClosed()

    assert app.api_ready.ready is False

    assert (
        app.api_ready.next_valid_order_id
        is None
    )


def test_connection_closed_does_not_forget_order_id_history() -> None:
    """Socket loss must not make old IB IDs reusable."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    assert app.order_id_allocator.allocate() == 100

    assert (
        app.order_id_allocator.next_order_id
        == 101
    )

    app.connectionClosed()

    assert (
        app.order_id_allocator.next_order_id
        == 101
    )

    assert (
        app.order_id_allocator.highest_allocated_order_id
        == 100
    )


def test_reconnect_next_valid_id_cannot_regress_allocator() -> None:
    """Stale reconnect ID must not cause order-ID reuse."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    assert app.order_id_allocator.allocate() == 100
    assert app.order_id_allocator.allocate() == 101

    app.connectionClosed()

    app.nextValidId(
        50
    )

    assert (
        app.order_id_allocator.next_order_id
        == 102
    )


def test_local_symbol_from_official_contract_is_used() -> None:
    """Official Contract.localSymbol should flow into BTS."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(
            symbol="MBT",
            local_symbol="MBTZ6",
        ),
        position=1,
        avgCost=60000.0,
    )

    app.positionEnd()

    assert (
        broker_client.get_raw_positions()[0].symbol
        == "MBTZ6"
    )


def test_empty_local_symbol_falls_back_to_root_symbol() -> None:
    """Root symbol should be used if localSymbol is unavailable."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(
            symbol="MBT",
            local_symbol="",
        ),
        position=1,
        avgCost=60000.0,
    )

    app.positionEnd()

    assert (
        broker_client.get_raw_positions()[0].symbol
        == "MBT"
    )