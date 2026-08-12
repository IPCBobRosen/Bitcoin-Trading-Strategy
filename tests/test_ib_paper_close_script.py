"""Offline safety tests for the first IB paper SELL_TO_CLOSE."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.ib_broker_client import (
    IBBrokerClient,
    IBPositionRecord,
)
from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.execution_ledger import ExecutionStatus

from app.ib_position_transport import IBPositionTransport
from scripts.test_ib_paper_close import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    EXPECTED_FINAL_POSITION,
    EXPECTED_INITIAL_POSITION,
    EXPECTED_LOCAL_SYMBOL,
    IBPaperCloseResult,
    PAPER_QUANTITY,
    SYMBOL,
    build_close_trade_request,
    get_mbt_position,
    main,
    print_result,
    require_exact_long_position,
    run_paper_close_test,
    validate_close_trade_request,
)


def create_trade_request(
    *,
    environment: Environment = Environment.STAGING,
    intent: TradeIntent = TradeIntent.SELL_TO_CLOSE,
    symbol: str = "MBT",
    quantity: int = 1,
) -> TradeRequest:
    """Create one deterministic paper-close TradeRequest."""

    return TradeRequest(
        event_id="event-close-001",
        signal_id="signal-close-001",
        timestamp=datetime(
            2026,
            8,
            12,
            13,
            45,
            tzinfo=timezone.utc,
        ),
        environment=environment,
        intent=intent,
        symbol=symbol,
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def create_success_result() -> IBPaperCloseResult:
    """Create one fully successful close result."""

    return IBPaperCloseResult(
        event_id="event-close-001",
        broker_order_id=3,
        initial_position=1,
        risk_approved=True,
        projected_position=0,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.FILLED,
        final_position=0,
        kill_switch_active=False,
    )


def complete_snapshot(
    broker_client: IBBrokerClient,
    *,
    positions: tuple[IBPositionRecord, ...] = (),
) -> None:
    """Complete a position snapshot through production broker state."""

    transport = IBPositionTransport(
        broker_client
    )

    transport.begin_snapshot()

    for position in positions:
        broker_client.receive_position(
            position
        )

    transport.position_end()


def test_symbol_is_mbt() -> None:
    """Paper close should be limited to MBT."""

    assert SYMBOL == "MBT"


def test_expected_local_symbol_is_mbtq6() -> None:
    """Close should recognize current August contract."""

    assert EXPECTED_LOCAL_SYMBOL == "MBTQ6"


def test_quantity_is_exactly_one() -> None:
    """Close harness must be limited to one contract."""

    assert PAPER_QUANTITY == 1


def test_expected_initial_position_is_long_one() -> None:
    """Close harness should require +1 MBT."""

    assert EXPECTED_INITIAL_POSITION == 1


def test_expected_final_position_is_flat() -> None:
    """SELL_TO_CLOSE must end at zero."""

    assert EXPECTED_FINAL_POSITION == 0


def test_contract_month_is_august_28_2026() -> None:
    """Harness should use TWS-confirmed expiry."""

    assert CONTRACT_MONTH == "20260828"


def test_build_request_uses_staging() -> None:
    """Paper close may not use LIVE environment."""

    request = build_close_trade_request(
        event_id="event-001"
    )

    assert (
        request.environment
        is Environment.STAGING
    )


def test_build_request_uses_sell_to_close() -> None:
    """Paper close must use explicit SELL_TO_CLOSE."""

    request = build_close_trade_request(
        event_id="event-001"
    )

    assert (
        request.intent
        is TradeIntent.SELL_TO_CLOSE
    )


def test_build_request_quantity_is_one() -> None:
    """Paper close request must contain one contract."""

    request = build_close_trade_request(
        event_id="event-001"
    )

    assert request.quantity == 1


def test_build_request_symbol_is_mbt() -> None:
    """Paper close should use root MBT symbol."""

    request = build_close_trade_request(
        event_id="event-001"
    )

    assert request.symbol == "MBT"


def test_empty_event_id_is_rejected() -> None:
    """Close event requires an audit identifier."""

    with pytest.raises(
        ValueError,
        match="'event_id'",
    ):
        build_close_trade_request(
            event_id=" "
        )


def test_live_environment_is_rejected() -> None:
    """Close harness may not masquerade as LIVE."""

    request = create_trade_request(
        environment=Environment.LIVE
    )

    with pytest.raises(
        RuntimeError,
        match="Environment.STAGING",
    ):
        validate_close_trade_request(
            request
        )


def test_quantity_two_is_rejected() -> None:
    """Close harness must not sell two contracts."""

    request = create_trade_request(
        quantity=2
    )

    with pytest.raises(
        RuntimeError,
        match="exactly 1 MBT",
    ):
        validate_close_trade_request(
            request
        )


@pytest.mark.parametrize(
    "intent",
    [
        TradeIntent.BUY_TO_OPEN,
        TradeIntent.SELL_TO_OPEN,
        TradeIntent.BUY_TO_CLOSE,
    ],
)
def test_non_sell_to_close_intent_is_rejected(
    intent: TradeIntent,
) -> None:
    """Only SELL_TO_CLOSE is permitted."""

    request = create_trade_request(
        intent=intent
    )

    with pytest.raises(
        RuntimeError,
        match="SELL_TO_CLOSE",
    ):
        validate_close_trade_request(
            request
        )


def test_non_mbt_symbol_is_rejected() -> None:
    """Harness must not close another product."""

    request = create_trade_request(
        symbol="BTC"
    )

    with pytest.raises(
        RuntimeError,
        match="only MBT",
    ):
        validate_close_trade_request(
            request
        )


def test_invalid_request_type_is_rejected() -> None:
    """Validator requires TradeRequest."""

    with pytest.raises(
        TypeError,
        match="'trade_request'",
    ):
        validate_close_trade_request(
            object()  # type: ignore[arg-type]
        )


def test_unarmed_close_refuses_to_run(
    tmp_path,
) -> None:
    """No IB connection should occur when harness is unarmed."""

    with pytest.raises(
        RuntimeError,
        match="not armed",
    ):
        run_paper_close_test(
            armed=False,
            ledger_path=(
                tmp_path
                / "ledger.db"
            ),
        )


def test_armed_value_must_be_bool(
    tmp_path,
) -> None:
    """Arming state must be explicit."""

    with pytest.raises(
        TypeError,
        match="'armed'",
    ):
        run_paper_close_test(
            armed=1,  # type: ignore[arg-type]
            ledger_path=(
                tmp_path
                / "ledger.db"
            ),
        )


def test_main_without_argument_refuses_close(
    capsys,
) -> None:
    """Normal execution without flag must not transmit."""

    result = main(
        []
    )

    output = (
        capsys.readouterr().out
    )

    assert result == 2

    assert (
        "PAPER CLOSE NOT SENT"
        in output
    )


def test_main_with_wrong_argument_refuses_close(
    capsys,
) -> None:
    """Wrong flag must not arm the close."""

    result = main(
        [
            "--yes",
        ]
    )

    output = (
        capsys.readouterr().out
    )

    assert result == 2
    assert ARMING_ARGUMENT in output


def test_success_result_reports_success() -> None:
    """Correct close lifecycle should report success."""

    assert (
        create_success_result().successful
        is True
    )


def test_nonfilled_close_is_not_successful() -> None:
    """Submitted/cancelled order is not successful close."""

    result = IBPaperCloseResult(
        event_id="event-close-001",
        broker_order_id=3,
        initial_position=1,
        risk_approved=True,
        projected_position=0,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.CANCELLED,
        final_position=1,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_nonflat_final_position_is_not_successful() -> None:
    """Execution success requires broker to be flat."""

    result = IBPaperCloseResult(
        event_id="event-close-001",
        broker_order_id=3,
        initial_position=1,
        risk_approved=True,
        projected_position=0,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.FILLED,
        final_position=1,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_wrong_initial_position_is_not_successful() -> None:
    """Harness must begin from exactly +1."""

    result = IBPaperCloseResult(
        event_id="event-close-001",
        broker_order_id=3,
        initial_position=2,
        risk_approved=True,
        projected_position=0,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.FILLED,
        final_position=0,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_kill_switch_invalidates_success() -> None:
    """Emergency state invalidates close test result."""

    result = IBPaperCloseResult(
        event_id="event-close-001",
        broker_order_id=3,
        initial_position=1,
        risk_approved=True,
        projected_position=0,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.FILLED,
        final_position=0,
        kill_switch_active=True,
    )

    assert result.successful is False


def test_result_is_immutable() -> None:
    """Close result must be immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_position = 1  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful close should clearly report flat account."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "FLAT" in output


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_completed_empty_snapshot_reports_zero() -> None:
    """Empty completed snapshot should be flat."""

    broker_client = IBBrokerClient()

    complete_snapshot(
        broker_client
    )

    assert (
        get_mbt_position(
            broker_client
        )
        == 0
    )


def test_exact_long_position_is_required() -> None:
    """A +1 MBT snapshot should satisfy the close prerequisite."""

    broker_client = IBBrokerClient()

    complete_snapshot(
        broker_client,
        positions=(
            IBPositionRecord(
                account="DU_TEST",
                symbol="MBT",
                position=1,
                local_symbol="MBTQ6",
                trading_class="MBT",
                last_trade_date="20260828",
                average_cost=65000.0,
            ),
        ),
    )

    assert (
        require_exact_long_position(
            broker_client
        )
        == 1
    )


def test_flat_position_is_rejected_for_sell_to_close() -> None:
    """SELL_TO_CLOSE cannot run while already flat."""

    broker_client = IBBrokerClient()

    complete_snapshot(
        broker_client
    )

    with pytest.raises(
        RuntimeError,
        match="requires exactly \\+1 MBT",
    ):
        require_exact_long_position(
            broker_client
        )


def test_short_position_is_rejected_for_sell_to_close() -> None:
    """A short position must never receive SELL_TO_CLOSE."""

    broker_client = IBBrokerClient()

    complete_snapshot(
        broker_client,
        positions=(
            IBPositionRecord(
                account="DU_TEST",
                symbol="MBT",
                position=-1,
                local_symbol="MBTQ6",
                trading_class="MBT",
                last_trade_date="20260828",
                average_cost=65000.0,
            ),
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="requires exactly \\+1 MBT",
    ):
        require_exact_long_position(
            broker_client
        )


def test_two_contract_long_is_rejected() -> None:
    """First close harness may not partially close +2."""

    broker_client = IBBrokerClient()

    complete_snapshot(
        broker_client,
        positions=(
            IBPositionRecord(
                account="DU_TEST",
                symbol="MBT",
                position=2,
                local_symbol="MBTQ6",
                trading_class="MBT",
                last_trade_date="20260828",
                average_cost=65000.0,
            ),
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="requires exactly \\+1 MBT",
    ):
        require_exact_long_position(
            broker_client
        )


def test_invalid_broker_client_is_rejected() -> None:
    """Position helper requires IBBrokerClient."""

    with pytest.raises(
        TypeError,
        match="'broker_client'",
    ):
        get_mbt_position(
            object()  # type: ignore[arg-type]
        )


def test_script_contains_transmit_true() -> None:
    """Paper close intentionally transmits after arming."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_close.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "transmit=True" in source


def test_script_contains_one_contract_limit() -> None:
    """Source should retain explicit quantity restriction."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_close.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "PAPER_QUANTITY = 1" in source


def test_script_requires_explicit_close_argument() -> None:
    """Paper close must require deliberate arming."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_close.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        "--confirm-paper-close"
        in source
    )