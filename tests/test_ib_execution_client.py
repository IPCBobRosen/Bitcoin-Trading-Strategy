"""Tests for the BTS Interactive Brokers execution coordinator."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from ibapi.contract import Contract
from ibapi.order import Order

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.duplicate_order_guard import DuplicateOrderGuard
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionStatus,
)
from app.ib_execution_client import (
    IBExecutionClient,
    IBSubmissionResult,
)
from app.ib_order_factory import IBOrderFactory


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


def create_factory() -> IBOrderFactory:
    """Create the offline-test IB order factory."""

    return IBOrderFactory(
        exchange="CMECRYPTO",
        currency="USD",
        trading_class="MBT",
        order_type="MKT",
        time_in_force="DAY",
        transmit=False,
    )


def create_client(
    tmp_path,
    *,
    place_order_function=None,
):
    """Create an isolated execution client and dependencies."""

    duplicate_guard = (
        DuplicateOrderGuard()
    )

    ledger = ExecutionLedger(
        tmp_path
        / "execution_ledger.db"
    )

    place_order = (
        place_order_function
        if place_order_function is not None
        else Mock()
    )

    client = IBExecutionClient(
        order_factory=create_factory(),
        duplicate_guard=duplicate_guard,
        execution_ledger=ledger,
        place_order_function=place_order,
    )

    return (
        client,
        duplicate_guard,
        ledger,
        place_order,
    )


def test_client_retains_dependencies(
    tmp_path,
) -> None:
    """Execution client should expose configured components."""

    (
        client,
        duplicate_guard,
        ledger,
        _,
    ) = create_client(
        tmp_path
    )

    assert (
        client.duplicate_guard
        is duplicate_guard
    )

    assert (
        client.execution_ledger
        is ledger
    )

    assert isinstance(
        client.order_factory,
        IBOrderFactory,
    )


def test_submit_returns_submission_result(
    tmp_path,
) -> None:
    """Successful submission should return typed result."""

    client, _, _, _ = create_client(
        tmp_path
    )

    result = client.submit(
        create_trade_request(),
        contract_month="202608",
        broker_order_id=100,
    )

    assert isinstance(
        result,
        IBSubmissionResult,
    )


def test_submit_preserves_event_id(
    tmp_path,
) -> None:
    """Submission result should retain Eagle event identity."""

    client, _, _, _ = create_client(
        tmp_path
    )

    result = client.submit(
        create_trade_request(
            event_id="event-123"
        ),
        contract_month="202608",
        broker_order_id=100,
    )

    assert result.event_id == "event-123"


def test_submit_preserves_broker_order_id(
    tmp_path,
) -> None:
    """Submission result should retain intended IB order ID."""

    client, _, _, _ = create_client(
        tmp_path
    )

    result = client.submit(
        create_trade_request(),
        contract_month="202608",
        broker_order_id=456,
    )

    assert result.broker_order_id == 456


def test_submit_calls_place_order_once(
    tmp_path,
) -> None:
    """Successful execution should invoke broker once."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    client.submit(
        create_trade_request(),
        contract_month="202608",
        broker_order_id=101,
    )

    assert place_order.call_count == 1


def test_place_order_receives_order_id_contract_and_order(
    tmp_path,
) -> None:
    """Injected broker function should receive official IB objects."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    client.submit(
        create_trade_request(),
        contract_month="202608",
        broker_order_id=102,
    )

    args = place_order.call_args.args

    assert args[0] == 102

    assert isinstance(
        args[1],
        Contract,
    )

    assert isinstance(
        args[2],
        Order,
    )


def test_place_order_receives_correct_contract(
    tmp_path,
) -> None:
    """Broker submission should receive constructed MBT contract."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    client.submit(
        create_trade_request(),
        contract_month="202608",
        broker_order_id=103,
    )

    contract = (
        place_order.call_args.args[1]
    )

    assert contract.symbol == "MBT"
    assert contract.secType == "FUT"

    assert (
        contract.lastTradeDateOrContractMonth
        == "202608"
    )


@pytest.mark.parametrize(
    (
        "intent_value",
        "expected_action",
    ),
    [
        (
            "BUY_TO_OPEN",
            "BUY",
        ),
        (
            "BUY_TO_CLOSE",
            "BUY",
        ),
        (
            "SELL_TO_OPEN",
            "SELL",
        ),
        (
            "SELL_TO_CLOSE",
            "SELL",
        ),
    ],
)
def test_all_trade_intents_reach_correct_ib_action(
    tmp_path,
    intent_value: str,
    expected_action: str,
) -> None:
    """Execution should preserve all four BTS intent directions."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    client.submit(
        create_trade_request(
            intent_value=intent_value
        ),
        contract_month="202608",
        broker_order_id=104,
    )

    order = (
        place_order.call_args.args[2]
    )

    assert order.action == expected_action


def test_submission_reserves_duplicate_guard(
    tmp_path,
) -> None:
    """Successful submission should consume in-memory event ID."""

    (
        client,
        duplicate_guard,
        _,
        _,
    ) = create_client(
        tmp_path
    )

    client.submit(
        create_trade_request(
            event_id="event-500"
        ),
        contract_month="202608",
        broker_order_id=105,
    )

    assert (
        duplicate_guard.contains(
            "event-500"
        )
        is True
    )


def test_submission_creates_durable_record(
    tmp_path,
) -> None:
    """Successful submission should persist execution state."""

    client, _, ledger, _ = (
        create_client(
            tmp_path
        )
    )

    client.submit(
        create_trade_request(
            event_id="event-600"
        ),
        contract_month="202608",
        broker_order_id=106,
    )

    record = ledger.get(
        "event-600"
    )

    assert record is not None


def test_successful_submission_is_marked_submitted(
    tmp_path,
) -> None:
    """Ledger should reach SUBMITTED before broker call returns."""

    client, _, ledger, _ = (
        create_client(
            tmp_path
        )
    )

    client.submit(
        create_trade_request(),
        contract_month="202608",
        broker_order_id=107,
    )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.SUBMITTED
    )

    assert record.broker_order_id == 107


def test_ledger_is_submitted_when_broker_function_runs(
    tmp_path,
) -> None:
    """Durable uncertain state must exist before placeOrder call."""

    database_path = (
        tmp_path
        / "execution_ledger.db"
    )

    observed_status = None

    def place_order(
        order_id,
        contract,
        order,
    ) -> None:
        nonlocal observed_status

        ledger_view = ExecutionLedger(
            database_path
        )

        record = ledger_view.get(
            "event-001"
        )

        assert record is not None

        observed_status = (
            record.status
        )

    duplicate_guard = (
        DuplicateOrderGuard()
    )

    ledger = ExecutionLedger(
        database_path
    )

    client = IBExecutionClient(
        order_factory=create_factory(),
        duplicate_guard=duplicate_guard,
        execution_ledger=ledger,
        place_order_function=place_order,
    )

    client.submit(
        create_trade_request(),
        contract_month="202608",
        broker_order_id=108,
    )

    assert (
        observed_status
        is ExecutionStatus.SUBMITTED
    )


def test_broker_exception_is_propagated(
    tmp_path,
) -> None:
    """Broker submission failures must not be hidden."""

    def failing_place_order(
        order_id,
        contract,
        order,
    ) -> None:
        raise RuntimeError(
            "Simulated IB transport failure."
        )

    client, _, _, _ = create_client(
        tmp_path,
        place_order_function=(
            failing_place_order
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="Simulated IB transport failure",
    ):
        client.submit(
            create_trade_request(),
            contract_month="202608",
            broker_order_id=109,
        )


def test_broker_exception_leaves_submitted_state(
    tmp_path,
) -> None:
    """Uncertain broker outcome must remain SUBMITTED."""

    def failing_place_order(
        order_id,
        contract,
        order,
    ) -> None:
        raise RuntimeError(
            "Connection dropped."
        )

    client, _, ledger, _ = (
        create_client(
            tmp_path,
            place_order_function=(
                failing_place_order
            ),
        )
    )

    with pytest.raises(
        RuntimeError,
    ):
        client.submit(
            create_trade_request(),
            contract_month="202608",
            broker_order_id=110,
        )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.SUBMITTED
    )

    assert record.broker_order_id == 110


def test_broker_exception_keeps_duplicate_reservation(
    tmp_path,
) -> None:
    """Uncertain submission must remain protected from retry."""

    def failing_place_order(
        order_id,
        contract,
        order,
    ) -> None:
        raise RuntimeError(
            "Connection dropped."
        )

    (
        client,
        duplicate_guard,
        _,
        _,
    ) = create_client(
        tmp_path,
        place_order_function=(
            failing_place_order
        ),
    )

    with pytest.raises(
        RuntimeError,
    ):
        client.submit(
            create_trade_request(
                event_id="event-700"
            ),
            contract_month="202608",
            broker_order_id=111,
        )

    assert (
        duplicate_guard.contains(
            "event-700"
        )
        is True
    )


def test_same_event_cannot_be_submitted_twice(
    tmp_path,
) -> None:
    """Duplicate Eagle event must never reach broker twice."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    trade_request = (
        create_trade_request()
    )

    client.submit(
        trade_request,
        contract_month="202608",
        broker_order_id=112,
    )

    with pytest.raises(
        ValueError,
        match="already exists",
    ):
        client.submit(
            trade_request,
            contract_month="202608",
            broker_order_id=113,
        )

    assert place_order.call_count == 1


def test_durable_duplicate_blocks_after_restart(
    tmp_path,
) -> None:
    """New process state must still reject previously submitted event."""

    database_path = (
        tmp_path
        / "execution_ledger.db"
    )

    first_place_order = Mock()

    first_client = IBExecutionClient(
        order_factory=create_factory(),
        duplicate_guard=DuplicateOrderGuard(),
        execution_ledger=ExecutionLedger(
            database_path
        ),
        place_order_function=first_place_order,
    )

    trade_request = (
        create_trade_request()
    )

    first_client.submit(
        trade_request,
        contract_month="202608",
        broker_order_id=114,
    )

    second_place_order = Mock()

    restarted_client = IBExecutionClient(
        order_factory=create_factory(),
        duplicate_guard=DuplicateOrderGuard(),
        execution_ledger=ExecutionLedger(
            database_path
        ),
        place_order_function=second_place_order,
    )

    with pytest.raises(
        ValueError,
        match="already exists",
    ):
        restarted_client.submit(
            trade_request,
            contract_month="202608",
            broker_order_id=115,
        )

    second_place_order.assert_not_called()


def test_order_factory_failure_marks_event_rejected(
    tmp_path,
) -> None:
    """Pre-broker construction failure should become REJECTED."""

    client, _, ledger, _ = (
        create_client(
            tmp_path
        )
    )

    with pytest.raises(
        ValueError,
        match="'contract_month'",
    ):
        client.submit(
            create_trade_request(),
            contract_month="invalid",
            broker_order_id=116,
        )

    record = ledger.get(
        "event-001"
    )

    assert record is not None

    assert (
        record.status
        is ExecutionStatus.REJECTED
    )

    assert record.reason is not None

    assert (
        "construction failed"
        in record.reason.lower()
    )


def test_order_factory_failure_does_not_call_broker(
    tmp_path,
) -> None:
    """Invalid IB package must never reach placeOrder."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    with pytest.raises(
        ValueError,
    ):
        client.submit(
            create_trade_request(),
            contract_month="bad",
            broker_order_id=117,
        )

    place_order.assert_not_called()


def test_ledger_reservation_failure_releases_memory_guard(
    tmp_path,
) -> None:
    """Failure before durable reservation should release memory lock."""

    (
        client,
        duplicate_guard,
        ledger,
        place_order,
    ) = create_client(
        tmp_path
    )

    trade_request = create_trade_request(
        event_id="event-800"
    )

    original_reserve = (
        ledger.reserve
    )

    def failing_reserve(
        request,
    ):
        raise RuntimeError(
            "Simulated SQLite failure."
        )

    ledger.reserve = failing_reserve  # type: ignore[method-assign]

    with pytest.raises(
        RuntimeError,
        match="Simulated SQLite failure",
    ):
        client.submit(
            trade_request,
            contract_month="202608",
            broker_order_id=118,
        )

    assert (
        duplicate_guard.contains(
            "event-800"
        )
        is False
    )

    place_order.assert_not_called()

    ledger.reserve = original_reserve  # type: ignore[method-assign]


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
    """IB order ID must be a non-negative integer."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    with pytest.raises(
        ValueError,
        match="'broker_order_id'",
    ):
        client.submit(
            create_trade_request(),
            contract_month="202608",
            broker_order_id=invalid_order_id,
        )

    place_order.assert_not_called()


def test_invalid_trade_request_is_rejected(
    tmp_path,
) -> None:
    """Execution client requires TradeRequest."""

    client, _, _, place_order = (
        create_client(
            tmp_path
        )
    )

    with pytest.raises(
        TypeError,
        match="'trade_request' must be a TradeRequest",
    ):
        client.submit(
            object(),  # type: ignore[arg-type]
            contract_month="202608",
            broker_order_id=119,
        )

    place_order.assert_not_called()


def test_invalid_factory_is_rejected(
    tmp_path,
) -> None:
    """Constructor requires IBOrderFactory."""

    with pytest.raises(
        TypeError,
        match="'order_factory' must be an IBOrderFactory",
    ):
        IBExecutionClient(
            order_factory=object(),  # type: ignore[arg-type]
            duplicate_guard=DuplicateOrderGuard(),
            execution_ledger=ExecutionLedger(
                tmp_path
                / "ledger.db"
            ),
            place_order_function=Mock(),
        )


def test_invalid_duplicate_guard_is_rejected(
    tmp_path,
) -> None:
    """Constructor requires DuplicateOrderGuard."""

    with pytest.raises(
        TypeError,
        match="'duplicate_guard' must be a DuplicateOrderGuard",
    ):
        IBExecutionClient(
            order_factory=create_factory(),
            duplicate_guard=object(),  # type: ignore[arg-type]
            execution_ledger=ExecutionLedger(
                tmp_path
                / "ledger.db"
            ),
            place_order_function=Mock(),
        )


def test_invalid_execution_ledger_is_rejected() -> None:
    """Constructor requires ExecutionLedger."""

    with pytest.raises(
        TypeError,
        match="'execution_ledger' must be an ExecutionLedger",
    ):
        IBExecutionClient(
            order_factory=create_factory(),
            duplicate_guard=DuplicateOrderGuard(),
            execution_ledger=object(),  # type: ignore[arg-type]
            place_order_function=Mock(),
        )


def test_non_callable_place_order_function_is_rejected(
    tmp_path,
) -> None:
    """Broker submission dependency must be callable."""

    with pytest.raises(
        TypeError,
        match="'place_order_function' must be callable",
    ):
        IBExecutionClient(
            order_factory=create_factory(),
            duplicate_guard=DuplicateOrderGuard(),
            execution_ledger=ExecutionLedger(
                tmp_path
                / "ledger.db"
            ),
            place_order_function=123,  # type: ignore[arg-type]
        )

def test_reserve_execution_creates_reserved_record_without_broker_call(
    tmp_path,
) -> None:
    """A pending execution may be durably reserved without reaching IB."""

    client, duplicate_guard, ledger, place_order = create_client(
        tmp_path
    )

    trade_request = create_trade_request(
        event_id="pending-exit-001",
        signal_id="signal-a",
        intent_value="SELL_TO_CLOSE",
    )

    record = client.reserve_execution(
        trade_request
    )

    assert record.event_id == "pending-exit-001"
    assert record.signal_id == "signal-a"
    assert record.status is ExecutionStatus.RESERVED
    assert record.broker_order_id is None

    assert ledger.contains(
        "pending-exit-001"
    )

    assert duplicate_guard.contains(
        "pending-exit-001"
    )

    place_order.assert_not_called()


def test_submit_reserved_execution_uses_exact_existing_reservation(
    tmp_path,
) -> None:
    """A known RESERVED execution may later be submitted exactly once."""

    client, _, ledger, place_order = create_client(
        tmp_path
    )

    trade_request = create_trade_request(
        event_id="pending-exit-002",
        signal_id="signal-a",
        intent_value="SELL_TO_CLOSE",
    )

    client.reserve_execution(
        trade_request
    )

    result = client.submit_reserved(
        trade_request,
        contract_month="202608",
        broker_order_id=200,
    )

    assert result.event_id == "pending-exit-002"
    assert result.broker_order_id == 200

    record = ledger.get(
        "pending-exit-002"
    )

    assert record is not None
    assert record.status is ExecutionStatus.SUBMITTED
    assert record.broker_order_id == 200

    assert place_order.call_count == 1


def test_submit_reserved_execution_cannot_submit_twice(
    tmp_path,
) -> None:
    """Once RESERVED becomes SUBMITTED it must never be blindly retried."""

    client, _, _, place_order = create_client(
        tmp_path
    )

    trade_request = create_trade_request(
        event_id="pending-exit-003",
        signal_id="signal-a",
        intent_value="SELL_TO_CLOSE",
    )

    client.reserve_execution(
        trade_request
    )

    client.submit_reserved(
        trade_request,
        contract_month="202608",
        broker_order_id=201,
    )

    with pytest.raises(
        ValueError,
        match="RESERVED",
    ):
        client.submit_reserved(
            trade_request,
            contract_month="202608",
            broker_order_id=202,
        )

    assert place_order.call_count == 1