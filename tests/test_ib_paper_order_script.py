"""Offline safety tests for the first IB paper-order harness."""

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
from app.execution_ledger import ExecutionStatus
from app.ib_broker_client import IBBrokerClient
from scripts.test_ib_paper_order import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    EXCHANGE,
    EXPECTED_LOCAL_SYMBOL,
    IBPaperOrderResult,
    PAPER_QUANTITY,
    SYMBOL,
    build_trade_request,
    get_mbt_position,
    main,
    print_result,
    require_completely_flat,
    run_paper_order_test,
    validate_trade_request,
)

from app.ib_position_transport import IBPositionTransport


def create_trade_request(
    *,
    environment: Environment = Environment.STAGING,
    intent: TradeIntent = TradeIntent.BUY_TO_OPEN,
    symbol: str = "MBT",
    quantity: int = 1,
) -> TradeRequest:
    """Create one paper-harness TradeRequest."""

    return TradeRequest(
        event_id="event-001",
        signal_id="signal-001",
        timestamp=datetime(
            2026,
            8,
            12,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        environment=environment,
        intent=intent,
        symbol=symbol,
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def create_success_result() -> IBPaperOrderResult:
    """Create a fully successful paper-order result."""

    return IBPaperOrderResult(
        event_id="event-001",
        broker_order_id=1,
        initial_position_count=0,
        initial_position=0,
        risk_approved=True,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.FILLED,
        final_position=1,
        kill_switch_active=False,
    )


def test_harness_symbol_is_mbt() -> None:
    """First paper order must use MBT."""

    assert SYMBOL == "MBT"


def test_expected_local_symbol_is_august_contract() -> None:
    """Broker verification should recognize MBTQ6."""

    assert EXPECTED_LOCAL_SYMBOL == "MBTQ6"


def test_harness_quantity_is_exactly_one() -> None:
    """First paper order is hard-limited to one contract."""

    assert PAPER_QUANTITY == 1


def test_harness_exchange_is_cme() -> None:
    """Micro Bitcoin futures should use CME."""

    assert EXCHANGE == "CME"


def test_contract_expiry_is_august_28_2026() -> None:
    """Harness should use the TWS-confirmed expiration."""

    assert CONTRACT_MONTH == "20260828"


def test_build_trade_request_uses_staging() -> None:
    """Paper request must use STAGING environment."""

    request = build_trade_request(
        event_id="event-001"
    )

    assert (
        request.environment
        is Environment.STAGING
    )


def test_build_trade_request_is_buy_to_open() -> None:
    """First paper request should create long exposure."""

    request = build_trade_request(
        event_id="event-001"
    )

    assert (
        request.intent
        is TradeIntent.BUY_TO_OPEN
    )


def test_build_trade_request_quantity_is_one() -> None:
    """Factory request may never exceed one contract."""

    request = build_trade_request(
        event_id="event-001"
    )

    assert request.quantity == 1


def test_build_trade_request_symbol_is_mbt() -> None:
    """Factory request should use MBT root symbol."""

    request = build_trade_request(
        event_id="event-001"
    )

    assert request.symbol == "MBT"


def test_empty_event_id_is_rejected() -> None:
    """Paper audit event ID is required."""

    with pytest.raises(
        ValueError,
        match="'event_id'",
    ):
        build_trade_request(
            event_id="   "
        )


def test_live_environment_is_rejected() -> None:
    """First harness may never submit LIVE environment."""

    request = create_trade_request(
        environment=Environment.LIVE
    )

    with pytest.raises(
        RuntimeError,
        match="Environment.STAGING",
    ):
        validate_trade_request(
            request
        )


def test_quantity_two_is_rejected() -> None:
    """Harness must refuse more than one MBT."""

    request = create_trade_request(
        quantity=2
    )

    with pytest.raises(
        RuntimeError,
        match="exactly 1 MBT",
    ):
        validate_trade_request(
            request
        )


@pytest.mark.parametrize(
    "intent",
    [
        TradeIntent.SELL_TO_OPEN,
        TradeIntent.BUY_TO_CLOSE,
        TradeIntent.SELL_TO_CLOSE,
    ],
)
def test_non_buy_to_open_intent_is_rejected(
    intent: TradeIntent,
) -> None:
    """First paper harness permits only BUY_TO_OPEN."""

    request = create_trade_request(
        intent=intent
    )

    with pytest.raises(
        RuntimeError,
        match="BUY_TO_OPEN",
    ):
        validate_trade_request(
            request
        )


def test_non_mbt_symbol_is_rejected() -> None:
    """Paper harness must not trade another product."""

    request = create_trade_request(
        symbol="BTC"
    )

    with pytest.raises(
        RuntimeError,
        match="only MBT",
    ):
        validate_trade_request(
            request
        )


def test_invalid_trade_request_type_is_rejected() -> None:
    """Validator requires TradeRequest."""

    with pytest.raises(
        TypeError,
        match="'trade_request'",
    ):
        validate_trade_request(
            object()  # type: ignore[arg-type]
        )


def test_unarmed_live_function_refuses_to_run(
    tmp_path,
) -> None:
    """No broker connection should occur when unarmed."""

    with pytest.raises(
        RuntimeError,
        match="not armed",
    ):
        run_paper_order_test(
            armed=False,
            ledger_path=(
                tmp_path
                / "ledger.db"
            ),
        )


def test_armed_value_must_be_bool(
    tmp_path,
) -> None:
    """Arming state must be explicit boolean."""

    with pytest.raises(
        TypeError,
        match="'armed'",
    ):
        run_paper_order_test(
            armed=1,  # type: ignore[arg-type]
            ledger_path=(
                tmp_path
                / "ledger.db"
            ),
        )


def test_main_without_argument_refuses_order(
    capsys,
) -> None:
    """Running the script normally must not transmit."""

    result = main(
        []
    )

    output = (
        capsys.readouterr().out
    )

    assert result == 2

    assert (
        "PAPER ORDER NOT SENT"
        in output
    )


def test_main_with_wrong_argument_refuses_order(
    capsys,
) -> None:
    """Only the exact arming phrase may enable live path."""

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
    """All expected states should constitute success."""

    assert (
        create_success_result().successful
        is True
    )


def test_nonfilled_result_is_not_successful() -> None:
    """Submission alone is not successful execution."""

    result = IBPaperOrderResult(
        event_id="event-001",
        broker_order_id=1,
        initial_position_count=0,
        initial_position=0,
        risk_approved=True,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.REJECTED,
        final_position=0,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_wrong_final_position_is_not_successful() -> None:
    """Fill must produce exactly +1 MBT."""

    result = IBPaperOrderResult(
        event_id="event-001",
        broker_order_id=1,
        initial_position_count=0,
        initial_position=0,
        risk_approved=True,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.FILLED,
        final_position=2,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_kill_switch_result_is_not_successful() -> None:
    """Active emergency state invalidates paper test."""

    result = IBPaperOrderResult(
        event_id="event-001",
        broker_order_id=1,
        initial_position_count=0,
        initial_position=0,
        risk_approved=True,
        readiness_passed=True,
        submitted_status=ExecutionStatus.SUBMITTED,
        final_status=ExecutionStatus.FILLED,
        final_position=1,
        kill_switch_active=True,
    )

    assert result.successful is False


def test_result_is_immutable() -> None:
    """Paper-order result should be immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_position = 0  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful test output should clearly say PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output

    assert (
        "remains OPEN"
        in output
    )


def test_print_result_requires_correct_type() -> None:
    """Printer must reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_empty_completed_broker_snapshot_is_flat() -> None:
    """Completed empty position snapshot should report zero MBT."""

    broker_client = IBBrokerClient()

    transport = IBPositionTransport(
        broker_client
    )

    transport.begin_snapshot()
    transport.position_end()

    assert (
        get_mbt_position(
            broker_client
        )
        == 0
    )


def test_empty_completed_snapshot_passes_flat_requirement() -> None:
    """Completed snapshot with no positions should satisfy flat check."""

    broker_client = IBBrokerClient()

    transport = IBPositionTransport(
        broker_client
    )

    transport.begin_snapshot()
    transport.position_end()

    require_completely_flat(
        broker_client
    )


def test_invalid_broker_client_type_is_rejected() -> None:
    """Position helper requires IBBrokerClient."""

    with pytest.raises(
        TypeError,
        match="'broker_client'",
    ):
        get_mbt_position(
            object()  # type: ignore[arg-type]
        )


def test_script_contains_explicit_transmit_true() -> None:
    """Real paper harness must intentionally enable transmission."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_order.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "transmit=True" in source


def test_script_contains_explicit_one_contract_limit() -> None:
    """Source should contain hard quantity tripwire."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_order.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "PAPER_QUANTITY = 1" in source


def test_script_requires_explicit_arming_argument() -> None:
    """Live path must require deliberate command-line arming."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_order.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        '--confirm-paper-order'
        in source
    )